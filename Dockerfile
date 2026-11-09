FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOTOONS_OUTPUTS_DIR=/data/outputs \
    NOTOONS_LOGS_DIR=/data/logs \
    NOTOONS_TEMP_DIR=/tmp/notoons/temp

WORKDIR /app

COPY requirements_prod.txt .
RUN pip install --no-cache-dir -r requirements_prod.txt

COPY notoons ./notoons

RUN mkdir -p /app/notoons/config /data/logs /data/outputs /tmp/notoons/temp \
    && chown -R 10001:10001 /app/notoons/config /data/logs /data/outputs /tmp/notoons

VOLUME ["/app/notoons/config", "/data/logs", "/data/outputs"]

EXPOSE 8000

USER 10001:10001

CMD ["python", "-m", "uvicorn", "notoons.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
