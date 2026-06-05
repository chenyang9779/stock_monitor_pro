FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STOCK_MONITOR_DATA_DIR=/data

WORKDIR /app

RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py database.py models.py services.py ./
COPY static ./static
COPY templates ./templates

EXPOSE 8000
VOLUME ["/data"]

CMD ["sh", "-c", "python -c 'from database import init_db; init_db()' && uvicorn app:app --host 0.0.0.0 --port 8000"]
