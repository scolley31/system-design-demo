"""Pub/sub server = chat shard owner（PDF 深入探討 1 + 2）。

每個 shard（hash(chat_id) % S）在同一時間只有一個 owner；同一個 chat 的所有訊息都經過它，所以：
- 它是訊息順序的唯一真實來源：在 per-chat lock 內指派 message_id = head + 1（連續序號）
- 先持久化（DynamoDB conditional put）才 fan-out、才回 SUCCESS → 耐久性
- 只 fan-out 給「有訂閱這個 chat 的 gateway」（M 個），不是 N 個使用者

狀態（全在記憶體，shard 搬家時丟棄、新 owner 從 DB lazy 重建）：
  owned / draining        我持有 / 正在交出的 shard
  subs[topic]             訂閱這個 topic 的 gateway（Peer）
  chats[chat_id]          ChatSeq：head、members、lock
  dedupe[chat_id]         最近的 client_msg_id → message_id（快速路徑；真正的關卡是 DB 的 client_msg 表）

fencing 三層（見 DESIGN 附錄 B）：
  1) etcd claim 是 CAS，同一 shard 只有一把 key
  2) keepalive 超過 TTL-1s 沒成功 → 自我 fencing，拒絕 publish
  3) DB 的 attribute_not_exists(message_id)：就算出現殭屍舊 owner，同一個號碼也只會被寫一次
"""
import asyncio
import math
import random
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from .config import DEDUPE_LRU, MAX_PARTICIPANTS, NUM_SHARDS, REBALANCE
from .errors import logger
from .shardmap import NodeInfo, Owner, ShardMap, chat_topic, shard_of
from .store import Appended, Duplicate, IdConflict, StoreError, run_db, store


@dataclass
class ChatSeq:
    head: int = 0
    members: set = field(default_factory=set)
    exists: bool = False
    loaded: bool = False
    name: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Peer:
    """一個 gateway 的內部連線。send() 不阻塞：塞進有上限的佇列，滿了回 False（慢消費者）。"""
    peer_id: str

    def send(self, frame: dict) -> bool:  # pragma: no cover - 介面
        raise NotImplementedError

    def close(self) -> None:
        pass


def failed(req_id, code: str, detail: str = "") -> dict:
    return {"type": "publish_failed", "req_id": req_id, "code": code, "detail": detail}


class PubSubNode:
    def __init__(self, info: NodeInfo, shardmap: ShardMap, member=None):
        self.info = info
        self.map = shardmap
        self.member = member          # EtcdMember；單 process 模式為 None（持有全部 shard）
        self.owned: set[int] = set()
        self.draining: set[int] = set()
        self.subs: dict[str, set[Peer]] = {}
        self.peer_topics: dict[Peer, set[str]] = {}
        self.chats: dict[str, ChatSeq] = {}
        self.dedupe: dict[str, OrderedDict] = {}
        self.fenced = False
        self.stats = {"published": 0, "dup": 0, "id_conflict": 0, "not_owner": 0, "fanout": 0, "slow_peer_drop": 0}
        self._last_claim = 0.0
        self._task: asyncio.Task | None = None
        if member is None:
            self.owned = set(range(NUM_SHARDS))

    # ------------------------------------------------------------ ownership
    def can_serve(self, shard: int) -> bool:
        if self.fenced or shard not in self.owned or shard in self.draining:
            return False
        return self.member is None or self.member.lease_healthy()

    def start(self):
        if self.member is not None:
            self._task = asyncio.create_task(self._ownership_loop())

    async def _ownership_loop(self):
        await asyncio.sleep(random.uniform(0, 0.3))  # jitter：兩台同時開機時不要整批搶
        while not self.fenced:
            try:
                await self._reconcile_ownership()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("ownership loop: %s", e)
            await self.map.wait_change(1.0)

    async def _reconcile_ownership(self):
        me = self.info.node_id
        # 1) 我以為持有、但 etcd 說不是我（lease 過期被刪 / 別人接手）→ 丟掉
        for s in list(self.owned):
            o = self.map.owner(s)
            if o is None or o.node_id != me:
                if time.monotonic() - self._last_claim > 2:  # 剛 claim 完 watch 可能還沒追上
                    logger.warning("shard %s 不再屬於我（etcd 顯示 %s）→ drop", s, o.node_id if o else None)
                    self._drop_shard(s)
        live = max(1, len(self.map.nodes))
        fair = math.ceil(NUM_SHARDS / live)
        # 2) 不夠公平份額 → 搶沒人持有的 shard（CAS）
        if len(self.owned) < fair and self.member.lease_healthy():
            orphans = [s for s in range(NUM_SHARDS) if self.map.owner(s) is None]
            random.shuffle(orphans)
            for s in orphans[: fair - len(self.owned)]:
                if await self.member.claim(s):
                    self.owned.add(s)
                    self._last_claim = time.monotonic()
                    self.map.set_owner(s, Owner(me, self.info.ws, self.info.http), self.map.revision)
                    self._forget_shard_state(s)  # 新接手：狀態一律從 DB 重建
                    logger.info("shard_takeover shard=%s node=%s", s, me)
        # 3) 持有太多（新節點加入）→ graceful 交出（2s 遲滯避免來回搶）
        elif REBALANCE and len(self.owned) > fair and time.monotonic() - self._last_claim > 2:
            for s in sorted(self.owned, reverse=True)[: len(self.owned) - fair]:
                await self.release_shard(s)

    async def release_shard(self, shard: int):
        self.draining.add(shard)  # 新 publish 立刻回 NOT_OWNER，gateway 會重試到新 owner
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
                cs.lock.locked() for cid, cs in self.chats.items() if shard_of(chat_topic(cid)) == shard):
            await asyncio.sleep(0.02)
        try:
            await self.member.release(shard)
        finally:
            self._drop_shard(shard)
            logger.info("shard_release shard=%s node=%s", shard, self.info.node_id)

    def _forget_shard_state(self, shard: int):
        for cid in [c for c in self.chats if shard_of(chat_topic(c)) == shard]:
            self.chats.pop(cid, None)
            self.dedupe.pop(cid, None)

    def _drop_shard(self, shard: int):
        self.owned.discard(shard)
        self.draining.discard(shard)
        self._forget_shard_state(shard)
        moved = [t for t in self.subs if shard_of(t) == shard]
        for t in moved:
            for p in self.subs.pop(t, set()):
                self.peer_topics.get(p, set()).discard(t)
        for p in list(self.peer_topics):
            p.send({"type": "shard_moved", "shards": [shard]})

    async def on_lease_lost(self):
        for s in list(self.owned):
            self._drop_shard(s)

    async def shutdown(self):
        """graceful 下線：先 fence（拒絕新 publish），再 revoke lease 讓 shard 立刻轉移。"""
        self.fenced = True
        if self._task:
            self._task.cancel()
        if self.member:
            await self.member.revoke()
        for p in list(self.peer_topics):
            p.send({"type": "shard_moved", "shards": sorted(self.owned)})

    # ------------------------------------------------------------ chat state
    async def load_chat(self, chat_id: str) -> ChatSeq:
        cs = self.chats.get(chat_id)
        if cs is None:
            cs = self.chats[chat_id] = ChatSeq()
        if not cs.loaded:
            async with cs.lock:
                if not cs.loaded:
                    info = (await run_db(store.get_chats, [chat_id])).get(chat_id)
                    if info:
                        cs.exists, cs.name = True, info.get("name") or ""
                        cs.members = set(await run_db(store.list_members, chat_id))
                        cs.head = await run_db(store.max_message_id, chat_id)  # consistent read
                    cs.loaded = True
        return cs

    def _dedupe_get(self, chat_id, cmid):
        d = self.dedupe.get(chat_id)
        return d.get(cmid) if d else None

    def _dedupe_put(self, chat_id, cmid, mid, created_at):
        d = self.dedupe.setdefault(chat_id, OrderedDict())
        d[cmid] = (mid, created_at)
        while len(d) > DEDUPE_LRU:
            d.popitem(last=False)

    def _fanout(self, topic: str, event: dict):
        frame = {"type": "deliver", "topic": topic, "event": event}
        for p in list(self.subs.get(topic, ())):
            if p.send(frame):
                self.stats["fanout"] += 1
            else:  # 慢消費者：斷掉它，gateway 重連後用 head 比對補送（不讓一個慢 gateway 拖垮整個 shard）
                self.stats["slow_peer_drop"] += 1
                logger.warning("peer %s send queue full → close", p.peer_id)
                p.close()

    # ------------------------------------------------------------ handlers
    async def handle(self, peer: Peer, f: dict) -> dict | None:
        t = f.get("type")
        if t == "subscribe":
            return await self._subscribe(peer, f)
        if t == "unsubscribe":
            self._unsubscribe(peer, f.get("topics", []))
            return None
        if t == "publish":
            return await self._publish(f)
        if t == "modify":
            return await self._modify(f)
        if t == "notify":
            return self._notify(f)
        return failed(f.get("req_id"), "BAD_FRAME", f"unknown internal type {t!r}")

    async def _subscribe(self, peer: Peer, f: dict) -> dict:
        ok, rejected = [], []
        for topic in f.get("topics", []):
            s = shard_of(topic)
            if not self.can_serve(s):
                rejected.append({"topic": topic, "code": "NOT_OWNER"})
                continue
            head = None
            if topic.startswith("c:"):
                try:
                    cs = await self.load_chat(topic[2:])
                    head = cs.head
                except StoreError:
                    rejected.append({"topic": topic, "code": "STORE_ERROR"})
                    continue
                if not self.can_serve(s):  # load 期間 shard 被搬走
                    rejected.append({"topic": topic, "code": "NOT_OWNER"})
                    continue
            self.subs.setdefault(topic, set()).add(peer)
            self.peer_topics.setdefault(peer, set()).add(topic)
            ok.append({"topic": topic, "head": head})
        return {"type": "subscribed", "req_id": f.get("req_id"), "ok": ok, "rejected": rejected}

    def _unsubscribe(self, peer: Peer, topics):
        for t in topics:
            self.subs.get(t, set()).discard(peer)
            if not self.subs.get(t):
                self.subs.pop(t, None)
            self.peer_topics.get(peer, set()).discard(t)

    def on_peer_closed(self, peer: Peer):
        self._unsubscribe(peer, list(self.peer_topics.get(peer, ())))
        self.peer_topics.pop(peer, None)

    async def _publish(self, f: dict) -> dict:
        rid, chat_id, cmid = f.get("req_id"), f["chat_id"], f["client_msg_id"]
        shard = shard_of(chat_topic(chat_id))
        if not self.can_serve(shard):
            self.stats["not_owner"] += 1
            return failed(rid, "NOT_OWNER")
        try:
            cs = await self.load_chat(chat_id)
        except StoreError as e:
            return failed(rid, "STORE_ERROR", str(e))
        if not cs.exists:
            return failed(rid, "CHAT_NOT_FOUND")
        if f["sender"] not in cs.members:
            return failed(rid, "NOT_MEMBER")
        hit = self._dedupe_get(chat_id, cmid)
        if hit:
            self.stats["dup"] += 1
            return {"type": "published", "req_id": rid, "chat_id": chat_id, "message_id": hit[0],
                    "created_at": hit[1], "dup": True}
        return await self._append_and_fanout(cs, shard, rid, chat_id, cmid, f["sender"], f.get("text", ""),
                                             "user", f.get("client_ts"))

    async def _append_and_fanout(self, cs, shard, rid, chat_id, cmid, sender, text, kind, client_ts) -> dict:
        async with cs.lock:
            for _attempt in range(2):
                if not self.can_serve(shard):  # 等鎖期間 shard 被搬走 / 自我 fencing
                    self.stats["not_owner"] += 1
                    return failed(rid, "NOT_OWNER")
                mid = cs.head + 1
                try:
                    res = await run_db(store.append_message, chat_id, mid, cmid, sender, text, kind, client_ts)
                except IdConflict:
                    # 這個號碼已經有人寫過 → 可能有殭屍舊 owner。重讀 head、確認自己仍是 owner 再試一次。
                    self.stats["id_conflict"] += 1
                    logger.warning("id_conflict chat=%s mid=%s node=%s", chat_id, mid, self.info.node_id)
                    cs.head = await run_db(store.max_message_id, chat_id)
                    if self.member is not None and not await self.member.verify_owner(shard):
                        self._drop_shard(shard)
                        return failed(rid, "NOT_OWNER")
                    continue
                except StoreError as e:
                    return failed(rid, "STORE_ERROR", str(e))  # 沒寫入 → 不 fan-out
                if isinstance(res, Duplicate):
                    cs.head = max(cs.head, res.message_id)
                    self._dedupe_put(chat_id, cmid, res.message_id, res.created_at)
                    self.stats["dup"] += 1
                    return {"type": "published", "req_id": rid, "chat_id": chat_id, "message_id": res.message_id,
                            "created_at": res.created_at, "dup": True}
                assert isinstance(res, Appended)
                cs.head = mid
                self._dedupe_put(chat_id, cmid, mid, res.created_at)
                self.stats["published"] += 1
                event = {"type": "newMessage", "chat_id": chat_id, "message_id": mid, "kind": kind, "text": text,
                         "created_by": sender, "created_at": res.created_at, "client_msg_id": cmid,
                         "client_ts": client_ts, "catchup": False, "owner": self.info.node_id}
                self._fanout(chat_topic(chat_id), event)  # 仍在鎖內 → fan-out 順序 = message_id 順序
                return {"type": "published", "req_id": rid, "chat_id": chat_id, "message_id": mid,
                        "created_at": res.created_at, "dup": False}
            return failed(rid, "STORE_ERROR", "id conflict persisted")

    async def _modify(self, f: dict) -> dict:
        """成員變更也走 chat owner，與訊息排在同一條序列上（系統訊息有自己的 message_id）。"""
        rid, chat_id, actor, target, op = f.get("req_id"), f["chat_id"], f["actor"], f["user_id"], f["operation"]
        shard = shard_of(chat_topic(chat_id))
        if not self.can_serve(shard):
            return failed(rid, "NOT_OWNER")
        try:
            cs = await self.load_chat(chat_id)
        except StoreError as e:
            return failed(rid, "STORE_ERROR", str(e))
        if not cs.exists:
            return failed(rid, "CHAT_NOT_FOUND")
        if actor not in cs.members:
            return failed(rid, "NOT_MEMBER")
        if op == "ADD":
            if target in cs.members:
                return {"type": "modified", "req_id": rid, "message_id": None, "noop": True}
            if len(cs.members) >= MAX_PARTICIPANTS:
                return failed(rid, "TOO_MANY_PARTICIPANTS", f"上限 {MAX_PARTICIPANTS} 人")
            # 新成員從目前 head 開始（不看加入前的歷史；PDF 未規定，見 DESIGN）
            await run_db(store.add_member, chat_id, target, cs.head)
            cs.members.add(target)
            text = f"{actor} 邀請 {target} 加入"
        elif op == "REMOVE":
            if target not in cs.members:
                return failed(rid, "NOT_MEMBER", f"{target} 不在聊天室")
            await run_db(store.remove_member, chat_id, target)
            cs.members.discard(target)
            text = f"{actor} 將 {target} 移出" if actor != target else f"{target} 離開了聊天室"
        else:
            return failed(rid, "BAD_FRAME", f"operation {op!r}")
        res = await self._append_and_fanout(cs, shard, rid, chat_id, f"sys:{uuid.uuid4().hex}", actor, text,
                                            "system", None)
        if res.get("type") != "published":
            return res
        return {"type": "modified", "req_id": rid, "message_id": res["message_id"], "noop": False,
                "chat": {"chat_id": chat_id, "name": cs.name, "participants": sorted(cs.members)}}

    def _notify(self, f: dict) -> dict:
        """u:{user} 控制事件（chatAdded / chatRemoved）：不持久，純 fan-out。"""
        topic = f["topic"]
        if not self.can_serve(shard_of(topic)):
            return failed(f.get("req_id"), "NOT_OWNER")
        self._fanout(topic, f["event"])
        return {"type": "notified", "req_id": f.get("req_id")}

    # ------------------------------------------------------------ debug
    def state(self) -> dict:
        return {
            "node_id": self.info.node_id,
            "owned": sorted(self.owned),
            "draining": sorted(self.draining),
            "fenced": self.fenced,
            "lease_healthy": self.member.lease_healthy() if self.member else True,
            "peers": [p.peer_id for p in self.peer_topics],
            "topics": len(self.subs),
            "chats_loaded": {c: cs.head for c, cs in list(self.chats.items())[:50] if cs.loaded},
            "stats": self.stats,
        }
