FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py database.py models.py services.py ./
COPY static ./static
COPY templates ./templates

EXPOSE 8000

CMD ["sh", "-c", "python -c 'from database import init_db; init_db()' && uvicorn app:app --host 0.0.0.0 --port 8000"]
