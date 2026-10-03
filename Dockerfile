FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

COPY pyproject.toml ./
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY entrypoint.sh ./

RUN pip install --no-cache-dir .

RUN chmod +x entrypoint.sh && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
