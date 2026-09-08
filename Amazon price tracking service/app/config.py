"""集中讀取環境變數（全部 env-gated；本機不設 → 原型路徑）。

各 env 對應 DESIGN「原型 vs Production」表。這裡只讀值，不做任何 I/O。
"""
import os


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


# --- 資料層 ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./price.db")
REDIS_URL = os.getenv("REDIS_URL")           # 有設 → Redis Streams（queue）
PRICE_TABLE = os.getenv("PRICE_TABLE")       # 有設 → DynamoDB price 表 + DynamoDB Streams（CDC）

# --- Crawler ---
MOCK_AMAZON = _bool("MOCK_AMAZON")           # 1 → 離線假 Amazon（CI / loadtest / 無網路）
ALLOW_SEED = _bool("ALLOW_SEED") or MOCK_AMAZON  # /debug/seed 是否開放
AMAZON_DOMAIN = os.getenv("AMAZON_DOMAIN", "www.amazon.com")
AMAZON_CURRENCY = os.getenv("AMAZON_CURRENCY", "USD")  # i18n-prefs cookie：固定顯示幣別
CRAWL_RPS = _float("CRAWL_RPS", 1.0)         # 每 process 對 Amazon 的請求速率（對齊 PDF 1 visit/sec/IP）
CRAWLER_PLAYWRIGHT = _bool("CRAWLER_PLAYWRIGHT")  # 1 且已安裝 → curl_cffi 失敗時升級 Playwright
FORCE_PLAYWRIGHT = _bool("FORCE_PLAYWRIGHT")      # debug：直接走 Playwright（驗 fallback）
FETCH_TIMEOUT_S = _float("FETCH_TIMEOUT_S", 20.0)

# --- Scheduler（優先式爬取）---
SCHEDULER_TICK_S = _float("SCHEDULER_TICK_S", 10.0)
SCHEDULER_BATCH = _int("SCHEDULER_BATCH", 3)
BASE_CRAWL_INTERVAL_S = _float("BASE_CRAWL_INTERVAL_S", 3600.0)
MIN_CRAWL_INTERVAL_S = _float("MIN_CRAWL_INTERVAL_S", 60.0)
MAX_CRAWL_INTERVAL_S = _float("MAX_CRAWL_INTERVAL_S", 86400.0)
PRIO_W_SUB = _float("PRIO_W_SUB", 10.0)
PRIO_W_VIEW = _float("PRIO_W_VIEW", 2.0)
PRIO_W_SUSPICIOUS = _float("PRIO_W_SUSPICIOUS", 100.0)
CRAWL_INFLIGHT_TTL_S = _float("CRAWL_INFLIGHT_TTL_S", 120.0)

# --- Extension 回報（眾包）---
SUSPICIOUS_DROP_PCT = _float("SUSPICIOUS_DROP_PCT", 0.30)   # 相對已知價下跌超過 → 可疑，先重爬
REPORT_TOLERANCE_PCT = _float("REPORT_TOLERANCE_PCT", 0.05) # 重爬結果與回報差距 ≤ → confirmed
REPORT_RATE_PER_MIN = _int("REPORT_RATE_PER_MIN", 30)       # 每個 reporter_token 每分鐘上限

# --- CDC / 通知 ---
CDC_POLL_INTERVAL_S = _float("CDC_POLL_INTERVAL_S", 1.0)
CDC_BATCH = _int("CDC_BATCH", 500)
MIN_CHANGE_PCT = _float("MIN_CHANGE_PCT", 0.01)   # 小於此幅度的波動不通知（除非跨越門檻）
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL", "")  # 有設 → email channel 走 SES

# --- 彙總（讀路徑）---
AGG_INTERVAL_S = _float("AGG_INTERVAL_S", 300.0)

ROLE = os.getenv("ROLE", "all")  # api | worker | all（原型全在同一 process；正式版預留分流）


def public_meta() -> dict:
    """給前端/維運看的公開設定（不含密鑰）。"""
    return {
        "mock_amazon": MOCK_AMAZON,
        "allow_seed": ALLOW_SEED,
        "amazon_domain": AMAZON_DOMAIN,
        "amazon_currency": AMAZON_CURRENCY,
        "crawl_rps": CRAWL_RPS,
        "playwright_enabled": CRAWLER_PLAYWRIGHT,
        "suspicious_drop_pct": SUSPICIOUS_DROP_PCT,
        "report_tolerance_pct": REPORT_TOLERANCE_PCT,
        "min_change_pct": MIN_CHANGE_PCT,
        "scheduler_tick_s": SCHEDULER_TICK_S,
        "base_crawl_interval_s": BASE_CRAWL_INTERVAL_S,
        "agg_interval_s": AGG_INTERVAL_S,
        "granularity_rules": {"<=90d": "daily", "<=1y": "weekly", ">1y": "monthly"},
        "price_backend": "dynamodb" if PRICE_TABLE else "sql",
        "queue_backend": "redis_streams" if REDIS_URL else "asyncio",
        "email_backend": "ses" if SES_FROM_EMAIL else "disabled",
    }
