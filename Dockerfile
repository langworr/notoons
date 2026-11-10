FROM python:3.11-slim

ARG PUID=1000
ARG PGID=1000

WORKDIR /app

COPY requirements_prod.txt .
RUN pip install --no-cache-dir -r requirements_prod.txt

COPY notoons ./notoons
COPY notoons/config/config_template.txt ./notoons/config/config_template.txt

RUN groupadd -g ${PGID} notoons_group \
    && useradd -u ${PUID} -g notoons_group -m notoons_user \
    && mkdir -p /app/notoons/config /app/notoons/logs /app/notoons/outputs /app/notoons/temp  \
    && chown -R ${PUID}:${PGID} /app/notoons/config /app/notoons/logs /app/notoons/outputs /app/notoons/temp

EXPOSE 8000

USER ${PUID}:${PGID}

CMD ["python", "-m", "uvicorn", "notoons.app.main:app", "--host", "0.0.0.0", "--port", "8000"]