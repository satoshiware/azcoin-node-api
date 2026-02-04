FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY .env.example /app/.env.example

ENV PYTHONPATH=/app/src
EXPOSE 8080

CMD ["uvicorn", "node_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
