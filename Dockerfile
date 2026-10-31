FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/trading.db
EXPOSE 8501
CMD ["sh","-c","python main.py & streamlit run dashboard.py --server.address=0.0.0.0 --server.port=8501"]
