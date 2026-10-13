FROM python:3.11-slim

# curl 供 HEALTHCHECK；其餘依賴皆有 wheel，無需編譯（刻意不裝 grpcio）
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

ENV PORT=8030 ROLE=all
EXPOSE 8030

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# 同一個 image：ROLE=gateway | pubsub | all。狀態在記憶體 → 每個 process 一個 worker。
# --ws-ping-interval：WebSocket 層 heartbeat（穿過 ALB / nginx idle timeout）
# --timeout-graceful-shutdown：有長連線時 SIGTERM 仍能在 5s 內結束（pub/sub 會先 revoke etcd lease）
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --ws-ping-interval 20 --ws-ping-timeout 20 --timeout-graceful-shutdown 5"]
