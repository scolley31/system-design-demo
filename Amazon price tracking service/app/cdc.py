"""CDC tailer（深入探討 2：從 pull 批次掃描改成 push 單一事件）。

寫入端只做一件事：append 到 price 表。tailer 持續 tail「變更日誌」，把每筆新價格轉成
price_changed 事件丟進 queue；通知 worker 不再掃表。

- SqlSeqTailer（原型）：以 prices.seq 單調遞增模擬 WAL 位置，每秒 `SELECT ... WHERE seq > checkpoint
  ORDER BY seq LIMIT n`，發完事件再存 checkpoint → at-least-once（重複由 outbox 冪等吸收）。
- DynamoStreamTailer（正式版）：DynamoDB Streams（NEW_IMAGE）。describe_stream 取 shards，每個 shard
  用 checkpoint（SequenceNumber）取 iterator，get_records 迴圈；每 60 秒重列 shards 撿 child shard。
  seq = ts epoch ms（同商品內單調）。附錄 B 比較 Lambda event source mapping / KCL adapter。

/debug/seed 回填歷史時會呼叫 advance_to() 把 checkpoint 推到回填列之後：回填不是「價格變動」，
不能觸發通知。
"""
import asyncio
import time
from datetime import datetime

from .config import CDC_BATCH, CDC_POLL_INTERVAL_S, PRICE_TABLE
from .database import SessionLocal
from .errors import logger
from .models import CdcCheckpoint
from .price_store import price_store
from .queue import queue


def _get_checkpoint(consumer: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(CdcCheckpoint, consumer)
        return row.position if row else None
    finally:
        db.close()


def _save_checkpoint(consumer: str, position: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(CdcCheckpoint, consumer)
        if row:
            row.position = position
        else:
            db.add(CdcCheckpoint(consumer=consumer, position=position))
        db.commit()
    finally:
        db.close()


class SqlSeqTailer:
    backend = "sql_seq"
    consumer = "prices_seq"

    def __init__(self):
        self.events_published = 0
        self.last_poll_at: float | None = None

    def checkpoint(self) -> int:
        return int(_get_checkpoint(self.consumer) or 0)

    def advance_to(self, seq: int) -> None:
        if seq > self.checkpoint():
            _save_checkpoint(self.consumer, str(seq))

    async def poll_once(self) -> int:
        cp = self.checkpoint()
        rows = await asyncio.to_thread(price_store.read_after, cp, CDC_BATCH)
        self.last_poll_at = time.time()
        if not rows:
            return 0
        for r in rows:
            await queue.enqueue("price_changed", r)
        _save_checkpoint(self.consumer, str(rows[-1]["seq"]))
        self.events_published += len(rows)
        return len(rows)

    async def run(self) -> None:
        logger.info("CDC tailer started (sql seq-poll, every %ss)", CDC_POLL_INTERVAL_S)
        while True:
            try:
                n = await self.poll_once()
                if n < CDC_BATCH:
                    await asyncio.sleep(CDC_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.exception("CDC tailer error: %s", e)
                await asyncio.sleep(CDC_POLL_INTERVAL_S)

    def status(self) -> dict:
        head = price_store.head_seq()
        cp = self.checkpoint()
        return {"backend": self.backend, "checkpoint": cp, "head_seq": head, "lag": max(0, head - cp),
                "poll_interval_s": CDC_POLL_INTERVAL_S, "events_published": self.events_published,
                "last_poll_at": self.last_poll_at}


class DynamoStreamTailer:
    backend = "dynamodb_streams"

    def __init__(self, table: str):
        import boto3  # 延遲匯入
        from boto3.dynamodb.types import TypeDeserializer
        self._ddb = boto3.client("dynamodb")
        self._streams = boto3.client("dynamodbstreams")
        self._deser = TypeDeserializer()
        self._table = table
        self._stream_arn = None
        self._iters: dict[str, str] = {}
        self.events_published = 0
        self.last_poll_at: float | None = None

    def advance_to(self, seq: int) -> None:
        pass  # Streams 路徑：回填走 batch_writer 也會產生 stream 記錄；正式版回填應直接寫彙總表（附錄 F）

    def _shards(self) -> list[dict]:
        if not self._stream_arn:
            self._stream_arn = self._ddb.describe_table(TableName=self._table)["Table"]["LatestStreamArn"]
        shards, last = [], None
        while True:
            kw = {"StreamArn": self._stream_arn}
            if last:
                kw["ExclusiveStartShardId"] = last
            d = self._streams.describe_stream(**kw)["StreamDescription"]
            shards.extend(d.get("Shards", []))
            last = d.get("LastEvaluatedShardId")
            if not last:
                return shards

    def _iterator(self, shard_id: str) -> str:
        cp = _get_checkpoint(f"ddb:{shard_id}")
        kw = {"StreamArn": self._stream_arn, "ShardId": shard_id}
        if cp:
            kw.update(ShardIteratorType="AFTER_SEQUENCE_NUMBER", SequenceNumber=cp)
        else:
            kw["ShardIteratorType"] = "TRIM_HORIZON"
        return self._streams.get_shard_iterator(**kw)["ShardIterator"]

    def _to_event(self, rec: dict) -> dict | None:
        if rec.get("eventName") != "INSERT":
            return None
        img = {k: self._deser.deserialize(v) for k, v in rec["dynamodb"]["NewImage"].items()}
        ts_ms = int(img["ts"])
        return {"seq": ts_ms, "product_id": img["product_id"], "price": float(img["price"]),
                "currency": img.get("currency", "USD"), "source": img.get("source", "crawler"),
                "ts": datetime.utcfromtimestamp(ts_ms / 1000).isoformat()}

    async def run(self) -> None:
        logger.info("CDC tailer started (DynamoDB Streams on %s)", self._table)
        last_list = 0.0
        while True:
            try:
                now = time.monotonic()
                if now - last_list > 60:
                    for s in await asyncio.to_thread(self._shards):
                        sid = s["ShardId"]
                        if sid not in self._iters and not s.get("SequenceNumberRange", {}).get("EndingSequenceNumber"):
                            self._iters[sid] = await asyncio.to_thread(self._iterator, sid)
                    last_list = now
                got = 0
                for sid, it in list(self._iters.items()):
                    resp = await asyncio.to_thread(self._streams.get_records, ShardIterator=it, Limit=CDC_BATCH)
                    self.last_poll_at = time.time()
                    for rec in resp.get("Records", []):
                        ev = self._to_event(rec)
                        if ev:
                            await queue.enqueue("price_changed", ev)
                            got += 1
                            self.events_published += 1
                        _save_checkpoint(f"ddb:{sid}", rec["dynamodb"]["SequenceNumber"])
                    nxt = resp.get("NextShardIterator")
                    if nxt:
                        self._iters[sid] = nxt
                    else:
                        self._iters.pop(sid, None)  # shard 關閉
                if got == 0:
                    await asyncio.sleep(CDC_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.exception("CDC (streams) error: %s", e)
                self._iters.clear()
                last_list = 0.0
                await asyncio.sleep(5)

    def status(self) -> dict:
        return {"backend": self.backend, "shards": list(self._iters.keys()), "events_published": self.events_published,
                "poll_interval_s": CDC_POLL_INTERVAL_S, "last_poll_at": self.last_poll_at}


def _build():
    return DynamoStreamTailer(PRICE_TABLE) if PRICE_TABLE else SqlSeqTailer()


tailer = _build()


async def run_tailer() -> None:
    await tailer.run()
