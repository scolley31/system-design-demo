"""append-only 價格歷史（env-gated）。

技術選型定案（見 DESIGN 技術選型表）：Price DB 是 high-write、append-only、以 product_id
分區 + timestamp 排序的存取模式 → 正式版 **DynamoDB**（PK=product_id、SK=ts epoch ms），
並開 **DynamoDB Streams**（NEW_IMAGE）當 CDC 來源。原型用同一個 SQLAlchemy engine 的
`prices` 表，`seq` autoincrement 模擬 WAL 位置讓 tailer 輪詢。

- 未設 PRICE_TABLE → SqlPriceStore（SQLite / Postgres）。
- 有設 PRICE_TABLE → DynamoPriceStore（boto3）。

事件 seq：SQL 路徑 = prices.seq；DynamoDB 路徑 = ts epoch ms（同一商品內單調遞增，
outbox 冪等鍵 hash(subscription_id|seq) 與 Δ 計算只需要「同商品內可比較」）。
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .config import PRICE_TABLE
from .database import SessionLocal
from .models import Price


class SqlPriceStore:
    backend = "sql"

    def append(self, product_id: str, price: float, currency: str = "USD",
               source: str = "crawler", fetcher: str | None = None, ts: datetime | None = None) -> int:
        db = SessionLocal()
        try:
            row = Price(product_id=product_id, price=price, currency=currency,
                        source=source, fetcher=fetcher, ts=ts or datetime.utcnow())
            db.add(row)
            db.commit()
            return row.seq
        finally:
            db.close()

    def bulk_append(self, rows: list[dict]) -> tuple[int, int]:
        """回填用（/debug/seed）。回傳 (min_seq, max_seq)。"""
        db = SessionLocal()
        try:
            before = db.execute(select(func.max(Price.seq))).scalar() or 0
            db.bulk_insert_mappings(Price, rows)
            db.commit()
            after = db.execute(select(func.max(Price.seq))).scalar() or 0
            return before + 1, after
        finally:
            db.close()

    def query_range(self, product_id: str, since: datetime, until: datetime | None = None) -> list[tuple[datetime, float]]:
        db = SessionLocal()
        try:
            q = select(Price.ts, Price.price).where(Price.product_id == product_id, Price.ts >= since)
            if until:
                q = q.where(Price.ts < until)
            return [(ts, p) for ts, p in db.execute(q.order_by(Price.ts)).all()]
        finally:
            db.close()

    def count_range(self, product_id: str, since: datetime) -> int:
        db = SessionLocal()
        try:
            return db.execute(
                select(func.count()).select_from(Price).where(Price.product_id == product_id, Price.ts >= since)
            ).scalar() or 0
        finally:
            db.close()

    def count_all(self, product_id: str) -> int:
        db = SessionLocal()
        try:
            return db.execute(select(func.count()).select_from(Price).where(Price.product_id == product_id)).scalar() or 0
        finally:
            db.close()

    def latest(self, product_id: str) -> tuple[datetime, float] | None:
        db = SessionLocal()
        try:
            row = db.execute(
                select(Price.ts, Price.price).where(Price.product_id == product_id).order_by(Price.ts.desc()).limit(1)
            ).first()
            return (row[0], row[1]) if row else None
        finally:
            db.close()

    # --- CDC tailer 用（只有 SQL 路徑有；DynamoDB 走 Streams）---
    def head_seq(self) -> int:
        db = SessionLocal()
        try:
            return db.execute(select(func.max(Price.seq))).scalar() or 0
        finally:
            db.close()

    def read_after(self, seq: int, limit: int) -> list[dict]:
        db = SessionLocal()
        try:
            rows = db.execute(
                select(Price).where(Price.seq > seq).order_by(Price.seq).limit(limit)
            ).scalars().all()
            return [
                {"seq": r.seq, "product_id": r.product_id, "price": r.price, "currency": r.currency,
                 "source": r.source, "ts": r.ts.isoformat()}
                for r in rows
            ]
        finally:
            db.close()


class DynamoPriceStore:
    """DynamoDB 後端（正式版）。表：hash_key=product_id(S)、range_key=ts(N, epoch ms)。
    Streams（NEW_IMAGE）由 cdc.DynamoStreamTailer 消費。
    """
    backend = "dynamodb"

    def __init__(self, table: str):
        import boto3  # 延遲匯入
        from boto3.dynamodb.conditions import Key
        self._Key = Key
        self._t = boto3.resource("dynamodb").Table(table)

    @staticmethod
    def _ms(ts: datetime) -> int:
        return int(ts.timestamp() * 1000)

    def append(self, product_id, price, currency="USD", source="crawler", fetcher=None, ts=None) -> int:
        from decimal import Decimal
        ts = ts or datetime.utcnow()
        ms = self._ms(ts)
        self._t.put_item(Item={
            "product_id": product_id, "ts": ms, "price": Decimal(str(price)),
            "currency": currency, "source": source, "fetcher": fetcher or "",
        })
        return ms

    def bulk_append(self, rows: list[dict]) -> tuple[int, int]:
        from decimal import Decimal
        mn, mx = None, None
        with self._t.batch_writer() as bw:
            for r in rows:
                ms = self._ms(r["ts"])
                mn = ms if mn is None else min(mn, ms)
                mx = ms if mx is None else max(mx, ms)
                bw.put_item(Item={"product_id": r["product_id"], "ts": ms, "price": Decimal(str(r["price"])),
                                  "currency": r.get("currency", "USD"), "source": r.get("source", "seed"), "fetcher": ""})
        return mn or 0, mx or 0

    def _query(self, product_id, since, until=None, count_only=False):
        cond = self._Key("product_id").eq(product_id)
        if until:
            cond = cond & self._Key("ts").between(self._ms(since), self._ms(until) - 1)
        else:
            cond = cond & self._Key("ts").gte(self._ms(since))
        kwargs = {"KeyConditionExpression": cond}
        if count_only:
            kwargs["Select"] = "COUNT"
        items, total = [], 0
        while True:
            resp = self._t.query(**kwargs)
            total += resp.get("Count", 0)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return total if count_only else items

    def query_range(self, product_id, since, until=None):
        return [(datetime.utcfromtimestamp(int(i["ts"]) / 1000), float(i["price"])) for i in self._query(product_id, since, until)]

    def count_range(self, product_id, since):
        return self._query(product_id, since, count_only=True)

    def count_all(self, product_id):
        return self._query(product_id, datetime(1970, 1, 1), count_only=True)

    def latest(self, product_id):
        resp = self._t.query(KeyConditionExpression=self._Key("product_id").eq(product_id),
                             ScanIndexForward=False, Limit=1)
        items = resp.get("Items", [])
        if not items:
            return None
        return datetime.utcfromtimestamp(int(items[0]["ts"]) / 1000), float(items[0]["price"])

    def head_seq(self) -> int:  # 無意義（tailer 走 Streams）；給 /cdc/status 用
        return self._ms(datetime.utcnow())

    def read_after(self, seq, limit):
        raise NotImplementedError("DynamoDB 路徑用 DynamoDB Streams（cdc.DynamoStreamTailer）")


def _build():
    return DynamoPriceStore(PRICE_TABLE) if PRICE_TABLE else SqlPriceStore()


price_store = _build()


def since_for_days(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)
