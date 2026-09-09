FROM python:3.11-slim

# curl 供 HEALTHCHECK；curl_cffi / psycopg2-binary 皆有 wheel，無需編譯
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 選配：Playwright 無頭 Chromium fallback（映像 +400MB）。
#   docker build --build-arg WITH_PLAYWRIGHT=1 .
ARG WITH_PLAYWRIGHT=0
RUN if [ "$WITH_PLAYWRIGHT" = "1" ]; then \
      pip install --no-cache-dir playwright==1.49.1 && playwright install --with-deps chromium; \
    fi
ENV CRAWLER_PLAYWRIGHT=$WITH_PLAYWRIGHT

COPY app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 注意：SSE 即時通知依賴行程內 realtime hub，需單一 process（見 realtime.py）；
# 背景迴圈（scheduler / CDC tailer / workers / aggregator）也在同一 process 內。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
