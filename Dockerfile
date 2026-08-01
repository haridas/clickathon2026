# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12
ARG TARGETPLATFORM
FROM --platform=$TARGETPLATFORM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY clickhouse_client.py config.py ./
COPY anomaly_detection ./anomaly_detection

RUN useradd --create-home --uid 10001 detector \
    && chown -R detector:detector /app
USER detector

CMD ["python", "-m", "anomaly_detection.service", "--check-every-seconds", "3600", "--metric", "revenue"]
