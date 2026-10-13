"""持久層（env-gated 工廠，比照 sibling price_store.py）。

PDF HLD 的四張表 + 一張去重表：
- chat         chat_id                         | name, created_by, created_at
- membership   chat_id (PK) + user_id (SK)     | 查一個 chat 的所有參與者
- message      chat_id (PK) + message_id (SK)  | message_id = per-chat 連續序號（owner 指派）
- chat_state   user_id (PK) + chat_id (SK)     | last_read_message_id；PK=user_id 兼當「我的 chat 清單」索引
- client_msg   chat_id (PK) + client_msg_id(SK)| → message_id；跨 shard 轉移仍有效的持久去重（PDF 沒有，見 DESIGN）

註：PDF 的 ack 流程一處寫更新 Membership、一處寫 ChatState 的 last_read_message_id；本專案統一放 ChatState。

- 未設 DYNAMO_TABLE_PREFIX → SqlStore（sqlite3，單 process dev 模式；createChat 是真交易）
- 有設 → DynamoStore（boto3；DYNAMO_ENDPOINT 有設時連 DynamoDB Local）

所有方法都是同步的；呼叫端用 run_db() 丟到專用 thread pool（boto3 是阻塞 I/O）。
"""
import asyncio
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .config import DATABASE_URL, DEDUPE_TTL_S, DYNAMO_ENDPOINT, DYNAMO_TABLE_PREFIX

_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="db")


async def run_db(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_executor, lambda: fn(*args))


class StoreError(Exception):
    """DB 暫時性錯誤（throttle / 連線）。訊息沒寫入，不可 fan-out。"""


class IdConflict(Exception):
    """同一個 (chat_id, message_id) 已被別人寫入 → fencing 訊號：可能有另一個 owner。"""


@dataclass
class Appended:
    message_id: int
    created_at: int


@dataclass
class Duplicate:
    message_id: int
    created_at: int


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- SQLite
class SqlStore:
    backend = "sqlite"

    def __init__(self, url: str):
        self._path = url.removeprefix("sqlite:///")
        self._local = threading.local()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self._path, timeout=30, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.row_factory = sqlite3.Row
            self._local.c = c
        return c

    def _init(self) -> None:
        self._conn().executescript("""
        CREATE TABLE IF NOT EXISTS chat (chat_id TEXT PRIMARY KEY, name TEXT, created_by TEXT, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS membership (chat_id TEXT, user_id TEXT, joined_at INTEGER,
            PRIMARY KEY (chat_id, user_id));
        CREATE TABLE IF NOT EXISTS chat_state (user_id TEXT, chat_id TEXT, last_read_message_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, chat_id));
        CREATE TABLE IF NOT EXISTS message (chat_id TEXT, message_id INTEGER, client_msg_id TEXT, created_by TEXT,
            text TEXT, kind TEXT, client_ts INTEGER, created_at INTEGER,
            PRIMARY KEY (chat_id, message_id), UNIQUE (chat_id, client_msg_id));
        """)

    def create_chat(self, chat_id, name, created_by, participants) -> None:
        c, ts = self._conn(), now_ms()
        c.execute("BEGIN IMMEDIATE")  # PDF：chat + 每位參與者的 membership 包在同一個交易
        try:
            c.execute("INSERT INTO chat VALUES (?,?,?,?)", (chat_id, name, created_by, ts))
            c.executemany("INSERT INTO membership VALUES (?,?,?)", [(chat_id, u, ts) for u in participants])
            c.executemany("INSERT OR IGNORE INTO chat_state VALUES (?,?,0)", [(u, chat_id) for u in participants])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def get_chats(self, chat_ids) -> dict:
        if not chat_ids:
            return {}
        q = ",".join("?" * len(chat_ids))
        rows = self._conn().execute(f"SELECT * FROM chat WHERE chat_id IN ({q})", list(chat_ids)).fetchall()
        return {r["chat_id"]: {"chat_id": r["chat_id"], "name": r["name"], "created_by": r["created_by"]} for r in rows}

    def list_members(self, chat_id) -> list[str]:
        return [r[0] for r in self._conn().execute(
            "SELECT user_id FROM membership WHERE chat_id=? ORDER BY user_id", (chat_id,))]

    def add_member(self, chat_id, user_id, last_read) -> None:
        c = self._conn()
        c.execute("INSERT OR IGNORE INTO membership VALUES (?,?,?)", (chat_id, user_id, now_ms()))
        c.execute("INSERT OR REPLACE INTO chat_state VALUES (?,?,?)", (user_id, chat_id, last_read))

    def remove_member(self, chat_id, user_id) -> None:
        c = self._conn()
        c.execute("DELETE FROM membership WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        c.execute("DELETE FROM chat_state WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    def append_message(self, chat_id, message_id, client_msg_id, created_by, text, kind, client_ts):
        c, ts = self._conn(), now_ms()
        try:
            c.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?,?)",
                      (chat_id, message_id, client_msg_id, created_by, text, kind, client_ts, ts))
            return Appended(message_id, ts)
        except sqlite3.IntegrityError:
            row = c.execute("SELECT message_id, created_at FROM message WHERE chat_id=? AND client_msg_id=?",
                            (chat_id, client_msg_id)).fetchone()
            if row:
                return Duplicate(row[0], row[1])
            raise IdConflict(f"{chat_id}#{message_id}")
        except sqlite3.OperationalError as e:
            raise StoreError(str(e)) from e

    def max_message_id(self, chat_id) -> int:
        return self._conn().execute("SELECT COALESCE(MAX(message_id),0) FROM message WHERE chat_id=?",
                                    (chat_id,)).fetchone()[0]

    def messages_after(self, chat_id, after_id, limit):
        rows = self._conn().execute(
            "SELECT * FROM message WHERE chat_id=? AND message_id>? ORDER BY message_id LIMIT ?",
            (chat_id, after_id, limit + 1)).fetchall()
        return [_row_msg(dict(r)) for r in rows[:limit]], len(rows) > limit

    def list_user_chats(self, user_id) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn().execute(
            "SELECT s.chat_id, s.last_read_message_id FROM chat_state s JOIN chat c ON c.chat_id=s.chat_id "
            "WHERE s.user_id=?", (user_id,))]

    def update_last_read(self, user_id, chat_id, mid) -> None:
        # 只前進不後退（ack 可能亂序抵達）
        self._conn().execute("UPDATE chat_state SET last_read_message_id=? "
                             "WHERE user_id=? AND chat_id=? AND last_read_message_id<?", (mid, user_id, chat_id, mid))

    def get_last_read(self, user_id, chat_id) -> int:
        r = self._conn().execute("SELECT last_read_message_id FROM chat_state WHERE user_id=? AND chat_id=?",
                                 (user_id, chat_id)).fetchone()
        return r[0] if r else 0


def _row_msg(r: dict) -> dict:
    return {"chat_id": r["chat_id"], "message_id": int(r["message_id"]), "client_msg_id": r.get("client_msg_id"),
            "created_by": r.get("created_by"), "text": r.get("text"), "kind": r.get("kind") or "user",
            "client_ts": int(r["client_ts"]) if r.get("client_ts") is not None else None,
            "created_at": int(r.get("created_at") or 0)}


# ---------------------------------------------------------------- DynamoDB
TABLES = {
    # name: (pk, pk_type, sk, sk_type)
    "chat": ("chat_id", "S", None, None),
    "membership": ("chat_id", "S", "user_id", "S"),
    "message": ("chat_id", "S", "message_id", "N"),
    "chat_state": ("user_id", "S", "chat_id", "S"),
    "client_msg": ("chat_id", "S", "client_msg_id", "S"),
}


class DynamoStore:
    """DynamoDB 後端（正式版 / DynamoDB Local）。

    createChat 的 101+ 項寫入超過 TransactWriteItems 上限（100 items）→ commit-marker：
      1) BatchWrite membership + chat_state（key 固定，可重試、冪等）
      2) 最後 conditional put chat 列 = commit 點
    讀取 list_user_chats 時以 chat 表 BatchGet 過濾孤兒（第 2 步沒成功的半成品），見 DESIGN 附錄 C。
    """
    backend = "dynamodb"

    def __init__(self, prefix: str, endpoint: str | None):
        import boto3  # 延遲匯入
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
        kw = {"endpoint_url": endpoint} if endpoint else {}
        self._c = boto3.client("dynamodb", **kw)
        self._ser, self._des = TypeSerializer(), TypeDeserializer()
        self.prefix = prefix
        self.t = {k: f"{prefix}{k}" for k in TABLES}

    # -- helpers
    def _item(self, d: dict) -> dict:
        return {k: self._ser.serialize(v) for k, v in d.items() if v is not None}

    def _plain(self, item: dict) -> dict:
        return {k: self._des.deserialize(v) for k, v in item.items()}

    def _batch_put(self, table: str, items: list[dict]) -> None:
        for i in range(0, len(items), 25):
            req = {table: [{"PutRequest": {"Item": self._item(it)}} for it in items[i:i + 25]]}
            for attempt in range(8):
                resp = self._c.batch_write_item(RequestItems=req)
                req = resp.get("UnprocessedItems") or {}
                if not req:
                    break
                time.sleep(0.05 * (2 ** attempt))
            else:
                raise StoreError("batch_write_item unprocessed items")

    def _wrap(self, fn):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return fn()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ProvisionedThroughputExceededException", "ThrottlingException",
                        "RequestLimitExceeded", "InternalServerError", "ServiceUnavailable"):
                raise StoreError(code) from e
            raise
        except BotoCoreError as e:
            raise StoreError(str(e)) from e

    # -- chats & membership
    def create_chat(self, chat_id, name, created_by, participants) -> None:
        ts = now_ms()
        self._wrap(lambda: self._batch_put(self.t["membership"],
                                           [{"chat_id": chat_id, "user_id": u, "joined_at": ts} for u in participants]))
        self._wrap(lambda: self._batch_put(self.t["chat_state"],
                                           [{"user_id": u, "chat_id": chat_id, "last_read_message_id": 0}
                                            for u in participants]))
        self._wrap(lambda: self._c.put_item(  # commit marker
            TableName=self.t["chat"], ConditionExpression="attribute_not_exists(chat_id)",
            Item=self._item({"chat_id": chat_id, "name": name, "created_by": created_by, "created_at": ts})))

    def get_chats(self, chat_ids) -> dict:
        out, ids = {}, list(dict.fromkeys(chat_ids))
        for i in range(0, len(ids), 100):
            keys = [{"chat_id": {"S": c}} for c in ids[i:i + 100]]
            req = {self.t["chat"]: {"Keys": keys, "ConsistentRead": True}}
            while req:
                resp = self._wrap(lambda: self._c.batch_get_item(RequestItems=req))
                for it in resp["Responses"].get(self.t["chat"], []):
                    p = self._plain(it)
                    out[p["chat_id"]] = {"chat_id": p["chat_id"], "name": p.get("name"), "created_by": p.get("created_by")}
                req = resp.get("UnprocessedKeys") or {}
        return out

    def _query_all(self, **kw) -> list[dict]:
        items, start = [], None
        while True:
            if start:
                kw["ExclusiveStartKey"] = start
            resp = self._wrap(lambda: self._c.query(**kw))
            items += [self._plain(i) for i in resp.get("Items", [])]
            start = resp.get("LastEvaluatedKey")
            if not start:
                return items

    def list_members(self, chat_id) -> list[str]:
        items = self._query_all(TableName=self.t["membership"], ConsistentRead=True,
                                KeyConditionExpression="chat_id = :c",
                                ExpressionAttributeValues={":c": {"S": chat_id}})
        return sorted(i["user_id"] for i in items)

    def add_member(self, chat_id, user_id, last_read) -> None:
        self._wrap(lambda: self._c.put_item(TableName=self.t["membership"],
                                            Item=self._item({"chat_id": chat_id, "user_id": user_id, "joined_at": now_ms()})))
        self._wrap(lambda: self._c.put_item(TableName=self.t["chat_state"],
                                            Item=self._item({"user_id": user_id, "chat_id": chat_id,
                                                             "last_read_message_id": last_read})))

    def remove_member(self, chat_id, user_id) -> None:
        self._wrap(lambda: self._c.delete_item(TableName=self.t["membership"],
                                               Key={"chat_id": {"S": chat_id}, "user_id": {"S": user_id}}))
        self._wrap(lambda: self._c.delete_item(TableName=self.t["chat_state"],
                                               Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}}))

    # -- messages
    def append_message(self, chat_id, message_id, client_msg_id, created_by, text, kind, client_ts):
        from botocore.exceptions import ClientError
        ts = now_ms()
        msg = self._item({"chat_id": chat_id, "message_id": message_id, "client_msg_id": client_msg_id,
                          "created_by": created_by, "text": text, "kind": kind, "client_ts": client_ts, "created_at": ts})
        dedupe = self._item({"chat_id": chat_id, "client_msg_id": client_msg_id, "message_id": message_id,
                             "created_at": ts, "expires_at": int(time.time()) + DEDUPE_TTL_S})
        try:
            self._c.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.t["message"], "Item": msg,
                         "ConditionExpression": "attribute_not_exists(message_id)"}},   # fencing：同號只能寫一次
                {"Put": {"TableName": self.t["client_msg"], "Item": dedupe,
                         "ConditionExpression": "attribute_not_exists(client_msg_id)"}},  # 去重：同 client_msg_id 只能一次
            ])
            return Appended(message_id, ts)
        except ClientError as e:
            err = e.response.get("Error", {})
            if err.get("Code") != "TransactionCanceledException":
                raise StoreError(err.get("Code", str(e))) from e
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
                got = self._c.get_item(TableName=self.t["client_msg"], ConsistentRead=True,
                                       Key={"chat_id": {"S": chat_id}, "client_msg_id": {"S": client_msg_id}})
                p = self._plain(got.get("Item", {}))
                if p:
                    return Duplicate(int(p["message_id"]), int(p.get("created_at", 0)))
            if reasons and reasons[0] == "ConditionalCheckFailed":
                raise IdConflict(f"{chat_id}#{message_id}") from e
            raise StoreError(f"transaction canceled: {reasons}") from e

    def max_message_id(self, chat_id) -> int:
        resp = self._wrap(lambda: self._c.query(
            TableName=self.t["message"], ConsistentRead=True, ScanIndexForward=False, Limit=1,
            KeyConditionExpression="chat_id = :c", ExpressionAttributeValues={":c": {"S": chat_id}},
            ProjectionExpression="message_id"))
        items = resp.get("Items", [])
        return int(items[0]["message_id"]["N"]) if items else 0

    def messages_after(self, chat_id, after_id, limit):
        resp = self._wrap(lambda: self._c.query(
            TableName=self.t["message"], ConsistentRead=True, Limit=limit + 1,
            KeyConditionExpression="chat_id = :c AND message_id > :a",
            ExpressionAttributeValues={":c": {"S": chat_id}, ":a": {"N": str(after_id)}}))
        items = [_row_msg(self._plain(i)) for i in resp.get("Items", [])]
        return items[:limit], len(items) > limit

    # -- read state
    def list_user_chats(self, user_id) -> list[tuple[str, int]]:
        items = self._query_all(TableName=self.t["chat_state"], ConsistentRead=True,
                                KeyConditionExpression="user_id = :u",
                                ExpressionAttributeValues={":u": {"S": user_id}})
        pairs = [(i["chat_id"], int(i.get("last_read_message_id", 0))) for i in items]
        live = self.get_chats([c for c, _ in pairs])  # 過濾 commit-marker 沒寫成的孤兒
        return [(c, lr) for c, lr in pairs if c in live]

    def update_last_read(self, user_id, chat_id, mid) -> None:
        from botocore.exceptions import ClientError
        try:
            self._c.update_item(
                TableName=self.t["chat_state"], Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}},
                UpdateExpression="SET last_read_message_id = :m",
                ConditionExpression="attribute_exists(user_id) AND last_read_message_id < :m",
                ExpressionAttributeValues={":m": {"N": str(mid)}})
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def get_last_read(self, user_id, chat_id) -> int:
        got = self._wrap(lambda: self._c.get_item(TableName=self.t["chat_state"], ConsistentRead=True,
                                                  Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}}))
        p = self._plain(got.get("Item", {}))
        return int(p.get("last_read_message_id", 0)) if p else 0

    # -- 建表（只給 DynamoDB Local / 手動用；正式版由 Terraform 建）
    def ensure_tables(self) -> list[str]:
        if not DYNAMO_ENDPOINT:
            raise RuntimeError("ensure_tables 只允許在 DYNAMO_ENDPOINT（DynamoDB Local）下執行；正式版用 Terraform")
        existing = set(self._c.list_tables().get("TableNames", []))
        created = []
        for name, (pk, pkt, sk, skt) in TABLES.items():
            tn = self.t[name]
            if tn in existing:
                continue
            attrs = [{"AttributeName": pk, "AttributeType": pkt}]
            keys = [{"AttributeName": pk, "KeyType": "HASH"}]
            if sk:
                attrs.append({"AttributeName": sk, "AttributeType": skt})
                keys.append({"AttributeName": sk, "KeyType": "RANGE"})
            self._c.create_table(TableName=tn, AttributeDefinitions=attrs, KeySchema=keys,
                                 BillingMode="PAY_PER_REQUEST")
            created.append(tn)
        return created


def _build():
    if DYNAMO_TABLE_PREFIX:
        return DynamoStore(DYNAMO_TABLE_PREFIX, DYNAMO_ENDPOINT)
    return SqlStore(DATABASE_URL)


store = _build()
