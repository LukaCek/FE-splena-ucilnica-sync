FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FE_ROOT=/data/FE SYNC_STATE_DIR=/state SYNC_INTERVAL_SECONDS=7200
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY fe_sync/ /app/fe_sync/
CMD ["python", "-m", "fe_sync", "loop"]
