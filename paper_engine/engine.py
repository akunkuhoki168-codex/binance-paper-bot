"""SQLite-backed multi-position paper execution core.

This module is simulation-only. It has no exchange client, credentials, or
order-submission code. Prices are supplied by the caller.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal

Side = Literal["LONG", "SHORT"]
AllocationMode = Literal["fixed", "compounding"]
BotState = Literal["IDLE", "RUNNING", "PAUSED", "STOPPING"]


@dataclass(frozen=True)
class PaperEngineSettings:
    initial_capital: float = 1000.0
    max_positions: int = 5
    leverage: int = 10
    allocation_mode: AllocationMode = "fixed"
    taker_fee_rate: float = 0.0005
    take_profit_usdt: float = 10.0
    stop_loss_usdt: float = 5.0
    cooldown_minutes: int = 15

    def __post_init__(self):
        if not isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if not isinstance(self.max_positions, int) or not 1 <= self.max_positions <= 5:
            raise ValueError("max_positions must be between 1 and 5")
        if not isinstance(self.leverage, int) or not 1 <= self.leverage <= 10:
            raise ValueError("leverage must be an integer from 1 to 10")
        if self.allocation_mode not in ("fixed", "compounding"):
            raise ValueError("allocation_mode must be fixed or compounding")
        if not isfinite(self.taker_fee_rate) or not 0 <= self.taker_fee_rate < 1:
            raise ValueError("taker_fee_rate must be in [0, 1)")
        if not isfinite(self.take_profit_usdt) or self.take_profit_usdt <= 0:
            raise ValueError("take_profit_usdt must be positive")
        if not isfinite(self.stop_loss_usdt) or self.stop_loss_usdt <= 0:
            raise ValueError("stop_loss_usdt must be positive")
        if not isinstance(self.cooldown_minutes, int) or self.cooldown_minutes < 0:
            raise ValueError("cooldown_minutes must be non-negative")


@dataclass
class PaperPosition:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    margin: float
    entry_fee: float
    funding_net: float
    opened_at: int


class PaperEngine:
    """Persistent paper account supporting isolated LONG/SHORT positions."""

    def __init__(self, db_path: str = "data/paper_engine.sqlite3",
                 settings: PaperEngineSettings | None = None):
        self.settings = settings or PaperEngineSettings()
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK(id=1),
                wallet REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0,
                fees_paid REAL NOT NULL DEFAULT 0,
                funding_net REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS paper_positions (
                symbol TEXT PRIMARY KEY,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                margin REAL NOT NULL,
                entry_fee REAL NOT NULL,
                funding_net REAL NOT NULL,
                opened_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL,
                net_pnl REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_cooldowns (
                symbol TEXT PRIMARY KEY,
                until_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_control (
                id INTEGER PRIMARY KEY CHECK(id=1),
                state TEXT NOT NULL DEFAULT 'IDLE',
                updated_at INTEGER NOT NULL
            );
        """)
        self._db.execute(
            "INSERT OR IGNORE INTO paper_account(id,wallet) VALUES(1,?)",
            (self.settings.initial_capital,),
        )
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT OR IGNORE INTO bot_control(id,state,updated_at) VALUES(1,'IDLE',?)",
            (now,),
        )
        self._db.commit()
        self._positions: dict[str, PaperPosition] = {}
        self._recover_positions()

    def _recover_positions(self):
        rows = self._db.execute("SELECT * FROM paper_positions").fetchall()
        self._positions = {
            row["symbol"]: PaperPosition(
                symbol=row["symbol"], side=row["side"], quantity=row["quantity"],
                entry_price=row["entry_price"], margin=row["margin"],
                entry_fee=row["entry_fee"], funding_net=row["funding_net"],
                opened_at=row["opened_at"],
            ) for row in rows
        }

    def close(self):
        with self._lock:
            self._db.close()

    def get_bot_state(self) -> BotState:
        with self._lock:
            row = self._db.execute("SELECT state FROM bot_control WHERE id=1").fetchone()
            return row["state"] if row else "IDLE"

    def set_bot_state(self, state: BotState) -> None:
        if state not in ("IDLE", "RUNNING", "PAUSED", "STOPPING"):
            raise ValueError("invalid bot state")
        with self._lock:
            self._db.execute(
                "UPDATE bot_control SET state=?, updated_at=? WHERE id=1",
                (state, int(time.time() * 1000)),
            )
            self._db.commit()

    def force_close_all(self, marks: dict[str, float]) -> dict[str, str]:
        """Close every open position at the supplied mark prices.

        Returns a dict of symbol -> result ('closed' or error message).
        """
        results = {}
        with self._lock:
            symbols = list(self._positions.keys())
            for symbol in symbols:
                price = marks.get(symbol)
                if price is None or not isfinite(price) or price <= 0:
                    results[symbol] = "missing or invalid mark price"
                    continue
                try:
                    self.close_position(symbol, price, reason="FORCE_CLOSE")
                    results[symbol] = "closed"
                except Exception as exc:
                    results[symbol] = str(exc)
        return results

    def _account(self):
        return self._db.execute("SELECT * FROM paper_account WHERE id=1").fetchone()

    def _available_margin(self) -> float:
        return float(self._account()["wallet"])

    def _allocation(self) -> float:
        if self.settings.allocation_mode == "fixed":
            return self.settings.initial_capital / self.settings.max_positions
        equity = self.equity()
        return max(0.0, equity / self.settings.max_positions)

    def equity(self, marks: dict[str, float] | None = None) -> float:
        with self._lock:
            wallet = float(self._account()["wallet"])
            locked = sum(p.margin for p in self._positions.values())
            unrealized = 0.0
            for symbol, position in self._positions.items():
                mark = (marks or {}).get(symbol, position.entry_price)
                unrealized += self._gross_pnl(position, mark)
            return wallet + locked + unrealized

    @staticmethod
    def _gross_pnl(position: PaperPosition, price: float) -> float:
        direction = 1.0 if position.side == "LONG" else -1.0
        return (price - position.entry_price) * position.quantity * direction

    def position_net_pnl(self, symbol: str, mark_price: float) -> float:
        self._validate_price(mark_price)
        with self._lock:
            p = self._positions[symbol.upper()]
            exit_fee = p.quantity * mark_price * self.settings.taker_fee_rate
            return self._gross_pnl(p, mark_price) - p.entry_fee - exit_fee + p.funding_net

    @staticmethod
    def _validate_price(price: float):
        if not isfinite(price) or price <= 0:
            raise ValueError("price must be finite and positive")

    def is_in_cooldown(self, symbol: str, now_ms: int | None = None) -> bool:
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        row = self._db.execute(
            "SELECT until_ms FROM paper_cooldowns WHERE symbol=?", (symbol.upper(),)
        ).fetchone()
        return bool(row and row["until_ms"] > now_ms)

    def open_position(self, symbol: str, side: Side, price: float,
                      now_ms: int | None = None) -> PaperPosition:
        symbol = symbol.strip().upper()
        self._validate_price(price)
        if not symbol or side not in ("LONG", "SHORT"):
            raise ValueError("valid symbol and LONG/SHORT side are required")
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            if symbol in self._positions:
                raise ValueError(f"{symbol} already has an open position")
            if len(self._positions) >= self.settings.max_positions:
                raise ValueError("maximum active position count reached")
            if self.is_in_cooldown(symbol, now_ms):
                raise ValueError(f"{symbol} is in take-profit cooldown")
            slot_budget = self._allocation()
            margin = slot_budget / (1.0 + self.settings.leverage * self.settings.taker_fee_rate)
            if margin <= 0:
                raise ValueError("no safe allocation available")
            notional = margin * self.settings.leverage
            quantity = notional / price
            entry_fee = notional * self.settings.taker_fee_rate
            if self._available_margin() < margin + entry_fee:
                raise ValueError("insufficient wallet for isolated margin and entry fee")
            p = PaperPosition(symbol, side, quantity, price, margin, entry_fee, 0.0, now_ms)
            with self._db:
                self._db.execute(
                    "UPDATE paper_account SET wallet=wallet-?, fees_paid=fees_paid+? WHERE id=1",
                    (margin + entry_fee, entry_fee),
                )
                self._db.execute(
                    "INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?)",
                    (symbol, side, quantity, price, margin, entry_fee, 0.0, now_ms),
                )
                self._db.execute(
                    "INSERT INTO paper_trades(symbol,side,action,quantity,price,fee,net_pnl,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (symbol, side, "OPEN", quantity, price, entry_fee, 0.0, "SIGNAL", now_ms),
                )
            self._positions[symbol] = p
            return p

    def apply_funding(self, symbol: str, funding_rate: float,
                      mark_price: float) -> float:
        symbol = symbol.strip().upper()
        self._validate_price(mark_price)
        if not isfinite(funding_rate):
            raise ValueError("funding_rate must be finite")
        with self._lock:
            p = self._positions[symbol]
            signed_payment = -p.quantity * mark_price * funding_rate
            if p.side == "SHORT":
                signed_payment = -signed_payment
            with self._db:
                self._db.execute(
                    "UPDATE paper_account SET wallet=wallet+?, funding_net=funding_net+? WHERE id=1",
                    (signed_payment, signed_payment),
                )
                self._db.execute(
                    "UPDATE paper_positions SET funding_net=funding_net+? WHERE symbol=?",
                    (signed_payment, symbol),
                )
            p.funding_net += signed_payment
            return signed_payment

    def close_position(self, symbol: str, price: float, reason: str = "MANUAL",
                       now_ms: int | None = None) -> float:
        symbol = symbol.strip().upper()
        self._validate_price(price)
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            p = self._positions[symbol]
            gross = self._gross_pnl(p, price)
            exit_fee = p.quantity * price * self.settings.taker_fee_rate
            net = gross - p.entry_fee - exit_fee + p.funding_net
            with self._db:
                self._db.execute(
                    "UPDATE paper_account SET wallet=wallet+? , realized_pnl=realized_pnl+?, fees_paid=fees_paid+? WHERE id=1",
                    (p.margin + gross - exit_fee, net, exit_fee),
                )
                self._db.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,))
                self._db.execute(
                    "INSERT INTO paper_trades(symbol,side,action,quantity,price,fee,net_pnl,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (symbol, p.side, "CLOSE", p.quantity, price, exit_fee, net, reason, now_ms),
                )
                if reason == "TAKE_PROFIT" and self.settings.cooldown_minutes:
                    self._db.execute(
                        "INSERT INTO paper_cooldowns(symbol,until_ms) VALUES(?,?) "
                        "ON CONFLICT(symbol) DO UPDATE SET until_ms=excluded.until_ms",
                        (symbol, now_ms + self.settings.cooldown_minutes * 60_000),
                    )
            del self._positions[symbol]
            return net

    def evaluate_exit(self, symbol: str, mark_price: float,
                      now_ms: int | None = None) -> str | None:
        symbol = symbol.strip().upper()
        self._validate_price(mark_price)

        with self._lock:
            if symbol not in self._positions:
                return None

            position = self._positions[symbol]
            net = self.position_net_pnl(symbol, mark_price)

            if net >= self.settings.take_profit_usdt:
                self.close_position(
                    symbol, mark_price, "TAKE_PROFIT", now_ms
                )
                return "TAKE_PROFIT"

            if net <= -self.settings.stop_loss_usdt:
                opposite_side = (
                    "SHORT" if position.side == "LONG" else "LONG"
                )
                self.close_position(
                    symbol, mark_price, "STOP_LOSS", now_ms
                )
                try:
                    self.open_position(
                        symbol, opposite_side, mark_price, now_ms
                    )
                    return "STOP_LOSS_REVERSED"
                except ValueError:
                    return "STOP_LOSS"

            return None

    def snapshot(self, marks: dict[str, float] | None = None) -> dict:
        with self._lock:
            account = self._account()
            return {
                "wallet": float(account["wallet"]),
                "equity": self.equity(marks),
                "realized_pnl": float(account["realized_pnl"]),
                "fees_paid": float(account["fees_paid"]),
                "funding_net": float(account["funding_net"]),
                "active_positions": len(self._positions),
                "bot_state": self.get_bot_state(),
                "positions": {
                    symbol: {
                        "side": p.side, "quantity": p.quantity,
                        "entry_price": p.entry_price, "margin": p.margin,
                        "entry_fee": p.entry_fee, "funding_net": p.funding_net,
                    } for symbol, p in self._positions.items()
                },
            }
