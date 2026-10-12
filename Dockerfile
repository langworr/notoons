FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements_prod.txt .
RUN pip install --no-cache-dir -r requirements_prod.txt

COPY notoons ./notoons

EXPOSE 8000

USER 10001:10001

CMD ["python", "-m", "uvicorn", "notoons.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
