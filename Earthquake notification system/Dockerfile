FROM python:3.11-slim

# psycopg2-binary 已含 wheel，無需編譯；curl 供 HEALTHCHECK 使用
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 注意：SSE 即時送達依賴行程內 realtime hub，需單一 process（見 realtime.py）。
# 因此用 uvicorn 單一 worker；正式版即時送達走 APNs/FCM，不受此限。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
