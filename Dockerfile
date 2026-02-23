FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt


FROM python:3.11-slim AS runtime

ARG VERSION=unknown
ARG VCS_REF=unknown
ARG OCI_SOURCE=https://github.com/<OWNER>/azcoin-api

LABEL org.opencontainers.image.title="azcoin-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${OCI_SOURCE}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    VERSION="${VERSION}" \
    GIT_SHA="${VCS_REF}"

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY --from=builder /opt/venv /opt/venv
COPY VERSION /app/VERSION
COPY src /app/src
COPY .env.example /app/.env.example

EXPOSE 8080

USER app

CMD ["uvicorn", "node_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
