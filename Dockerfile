FROM python:3.11-slim

ARG VERSION
ARG GIT_SHA

LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$GIT_SHA

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV VERSION=$VERSION
ENV GIT_SHA=$GIT_SHA

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY .env.example /app/.env.example

ENV PYTHONPATH=/app/src
EXPOSE 8080

CMD ["uvicorn", "node_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
