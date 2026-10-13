"""集中讀取環境變數（全部 env-gated；本機不設 → 單 process dev 模式）。

各 env 對應 DESIGN「原型 vs Production」表。這裡只讀值，不做任何 I/O。

角色（同一個 image，靠 ROLE 分流）：
- all     單 process：gateway + pub/sub 同在一個 uvicorn（SQLite + 記憶體 registry + LocalLink）
- gateway 對外 WebSocket 入口（stateless，可任意擴縮）
- pubsub  chat shard owner：訂閱表 + per-chat 序號 + 持久化 + fan-out
"""
import os


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


ROLE = os.getenv("ROLE", "all")                    # all | gateway | pubsub
NODE_ID = os.getenv("NODE_ID", "dev")
PORT = _int("PORT", 8030)
# pubsub 寫進 etcd 給 gateway 連的內部位址（compose：ws://pubsub-1:8041/internal/ws）
ADVERTISE_WS = os.getenv("ADVERTISE_WS", f"ws://127.0.0.1:{PORT}/internal/ws")
ADVERTISE_HTTP = os.getenv("ADVERTISE_HTTP", f"http://127.0.0.1:{PORT}")

# --- Service discovery ---
ETCD_URL = os.getenv("ETCD_URL")                   # 有設 → EtcdRegistry；未設 → 記憶體（單節點持有全部 shard）
ETCD_PREFIX = os.getenv("ETCD_PREFIX", "/messenger/")
LEASE_TTL_S = _int("LEASE_TTL_S", 5)
KEEPALIVE_EVERY_S = _float("KEEPALIVE_EVERY_S", max(0.5, LEASE_TTL_S / 3))
NUM_SHARDS = _int("NUM_SHARDS", 16)                # 正式版 4096（見 DESIGN 附錄 A）
REBALANCE = _bool("REBALANCE", "1")                # 新節點加入時，持有過多者 graceful release

# --- 資料層 ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./messenger.db")
DYNAMO_TABLE_PREFIX = os.getenv("DYNAMO_TABLE_PREFIX")  # 有設 → DynamoDB（表名 {prefix}chat …）
DYNAMO_ENDPOINT = os.getenv("DYNAMO_ENDPOINT")          # 有設 → DynamoDB Local（才允許 ensure_tables 建表）

# --- 行為參數 ---
MAX_PARTICIPANTS = _int("MAX_PARTICIPANTS", 100)        # PDF：群聊上限 100 人
MAX_TEXT_BYTES = _int("MAX_TEXT_BYTES", 4096)
CATCHUP_LIMIT = _int("CATCHUP_LIMIT", 200)              # 重連時每個 chat 一次補收多少則
ACK_FLUSH_MS = _int("ACK_FLUSH_MS", 500)                # 合併 ack 再寫 chat_state
PUBLISH_ATTEMPT_TIMEOUT_S = _float("PUBLISH_ATTEMPT_TIMEOUT_S", 2.0)
PUBLISH_DEADLINE_S = _float("PUBLISH_DEADLINE_S", 8.0)  # 超過 → FAILURE UNAVAILABLE（涵蓋一次 failover）
SEND_QUEUE_MAX = _int("SEND_QUEUE_MAX", 1000)           # 每條連線的送出佇列；滿了視為慢消費者斷線
DEDUPE_LRU = _int("DEDUPE_LRU", 1000)                   # 每 chat 記住最近多少個 client_msg_id
DEDUPE_TTL_S = _int("DEDUPE_TTL_S", 86400)              # client_msg 表的 TTL

ALLOW_DEBUG = _bool("ALLOW_DEBUG", "1" if ROLE == "all" else "0")  # /api/debug/kill 等
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def store_backend() -> str:
    return "dynamodb" if DYNAMO_TABLE_PREFIX else "sqlite"


def public_meta() -> dict:
    """給前端/維運看的公開設定（不含密鑰）。"""
    return {
        "role": ROLE,
        "node_id": NODE_ID,
        "mode": "single" if ROLE == "all" else "cluster",
        "store_backend": store_backend(),
        "registry_backend": "etcd" if ETCD_URL else "memory",
        "num_shards": NUM_SHARDS,
        "lease_ttl_s": LEASE_TTL_S,
        "max_participants": MAX_PARTICIPANTS,
        "allow_debug": ALLOW_DEBUG,
    }
