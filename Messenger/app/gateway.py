"""Gateway：對外 WebSocket 入口（stateless，使用者可以連任意一台）。

職責：
- client 協定（createChat / sendMessage / modifyChatParticipants / fetchMessages / listChats / ack）
- 連線表：user_id → 連線；chat_id → 本台上有在看這個 chat 的連線
- 訂閱管理：topic 引用計數（0→1 才向 owner subscribe、1→0 才 unsubscribe）
  每個 chat 只向它的 owner 訂一次，不管本台有幾個成員 → owner 的 fan-out 是對 M 個 gateway
- 離線補收：連上時「先訂閱、再補收」（中間不會有空窗），從 chat_state.last_read_message_id 之後推
- 送訊重試：owner 掛了 / 搬家時，用同一個 client_msg_id 重試到新 owner（owner 端去重）
- shard 搬家後：對新 owner 重訂閱，用 subscribed 回的 head 跟本台 last_forwarded 比對，缺的補送
- ack 合併：每 ACK_FLUSH_MS 寫一次 chat_state（重連時最多重送這段時間內的訊息，client 依 id 去重）
"""
import asyncio
import json
import re
import time
import uuid
from collections import Counter, defaultdict

from .config import (ACK_FLUSH_MS, CATCHUP_LIMIT, MAX_PARTICIPANTS, MAX_TEXT_BYTES, PUBLISH_ATTEMPT_TIMEOUT_S,
                     PUBLISH_DEADLINE_S, SEND_QUEUE_MAX)
from .errors import logger
from .links import LinkClosed
from .shardmap import ShardMap, chat_topic, shard_of, user_topic
from .store import StoreError, run_db, store

USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
RETRYABLE = {"NOT_OWNER", "UNAVAILABLE"}


class ClientConn:
    _seq = 0

    def __init__(self, ws, user_id: str):
        ClientConn._seq += 1
        self.conn_id = ClientConn._seq
        self.ws, self.user_id = ws, user_id
        self.chats: set[str] = set()
        self.q: asyncio.Queue = asyncio.Queue(SEND_QUEUE_MAX)
        self.send_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.floor: dict[str, int] = {}   # chat → 已推給這條連線的最大 message_id（擋掉「補收 vs live」的重疊）
        self.alive = True
        self._writer = asyncio.create_task(self._drain())

    def send(self, frame: dict) -> None:
        if not self.alive:
            return
        try:
            self.q.put_nowait(frame)
        except asyncio.QueueFull:  # 慢 client：斷線，重連時補收
            self.alive = False
            asyncio.create_task(self.ws.close(code=1013))

    async def _drain(self):
        try:
            while True:
                await self.ws.send_text(json.dumps(await self.q.get(), ensure_ascii=False))
        except Exception:  # noqa: BLE001
            self.alive = False

    def close(self):
        self.alive = False
        self._writer.cancel()


class GatewayNode:
    def __init__(self, node_id: str, shardmap: ShardMap, link_factory, view=None):
        self.node_id = node_id
        self.map = shardmap
        self.view = view                    # ClusterView（cluster 模式）；單 process 為 None
        self._link_factory = link_factory   # (node_id, ws_url) -> Link
        self.links: dict = {}
        self.conns: dict[str, set[ClientConn]] = defaultdict(set)
        self.chat_conns: dict[str, set[ClientConn]] = defaultdict(set)
        self.refs: Counter = Counter()
        self.sub_at: dict[str, str] = {}    # topic → 目前在哪個 pub/sub 節點訂閱著
        self.last_forwarded: dict[str, int] = {}
        self.ack_buf: dict[tuple, int] = {}
        self._dirty = asyncio.Event()
        self._rec_lock = asyncio.Lock()
        self.stats = Counter()
        self._tasks: list[asyncio.Task] = []
        self._trace = __import__("os").getenv("TRACE_FWD") == "1"
        self.map.on_change(self._on_map_change)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._ensure_links()
        self._tasks += [asyncio.create_task(self._reconcile_loop()), asyncio.create_task(self._ack_flush_loop())]

    async def stop(self):
        await self._flush_acks()
        for t in self._tasks:
            t.cancel()
        for link in self.links.values():
            await link.close()

    # ------------------------------------------------------------ links & subscriptions
    def _ensure_links(self):
        for nid, info in list(self.map.nodes.items()):
            if nid not in self.links and info.ws:
                self.links[nid] = self._link_factory(nid, info.ws)
        for nid in [n for n in self.links if n not in self.map.nodes]:
            link = self.links.pop(nid)
            asyncio.create_task(link.close())
            self._forget_node(nid)

    def _forget_node(self, nid: str):
        for t in [t for t, n in self.sub_at.items() if n == nid]:
            del self.sub_at[t]
        self._dirty.set()

    def _on_map_change(self, changed: dict):
        self._ensure_links()
        if changed:
            for t in [t for t in self.sub_at if shard_of(t) in changed]:
                o = self.map.owner(shard_of(t))
                if o is None or o.node_id != self.sub_at[t]:
                    del self.sub_at[t]
            self.stats["shard_changes"] += len(changed)
        self._dirty.set()

    async def on_link_up(self, nid: str):
        self._dirty.set()

    async def on_link_down(self, nid: str):
        self._forget_node(nid)

    async def on_link_frame(self, nid: str, f: dict):
        t = f.get("type")
        if t == "deliver":
            await self._on_deliver(f["topic"], f["event"])
        elif t == "shard_moved":
            shards = set(f.get("shards", []))
            for topic in [x for x, n in self.sub_at.items() if n == nid and shard_of(x) in shards]:
                del self.sub_at[topic]
            if self.view:
                self.view.refresh()
            self._dirty.set()

    def _ref(self, topic: str, delta: int):
        self.refs[topic] += delta
        if self.refs[topic] <= 0:
            del self.refs[topic]
            if topic.startswith("c:"):  # 本台已沒人在看：不再追蹤，下次有人連上走正常補收（不是搬家補送）
                self.last_forwarded.pop(topic[2:], None)
        self._dirty.set()

    async def _reconcile_loop(self):
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
            self._dirty.clear()
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("reconcile: %s", e)

    async def reconcile(self):
        """讓「實際訂閱」收斂到「應該訂閱」：缺的向 owner 訂，多的退訂。"""
        async with self._rec_lock:
            want = set(self.refs)
            for t in [t for t in self.sub_at if t not in want]:
                link = self.links.get(self.sub_at.pop(t))
                if link:
                    link.send({"type": "unsubscribe", "topics": [t]})
            by_node: dict[str, list[str]] = defaultdict(list)
            for t in want:
                o = self.map.owner_of_topic(t)
                if o and self.sub_at.get(t) != o.node_id:
                    by_node[o.node_id].append(t)
            await asyncio.gather(*(self._subscribe_on(nid, ts) for nid, ts in by_node.items()))

    async def _subscribe_on(self, nid: str, topics: list[str]):
        link = self.links.get(nid)
        if not link or not link.connected:
            return
        try:
            resp = await link.request({"type": "subscribe", "req_id": uuid.uuid4().hex, "topics": topics},
                                      PUBLISH_ATTEMPT_TIMEOUT_S)
        except (LinkClosed, asyncio.TimeoutError):
            return
        for item in resp.get("ok", []):
            topic, head = item["topic"], item.get("head")
            self.sub_at[topic] = nid
            if topic.startswith("c:") and head is not None:
                await self._repair(topic[2:], head)
        if resp.get("rejected"):
            self.stats["subscribe_rejected"] += len(resp["rejected"])

    async def _repair(self, chat_id: str, head: int):
        """搬家空窗期間 owner 可能已經送出（但本台沒收到）的訊息：用 head 比對，從 DB 補。"""
        lf = self.last_forwarded.get(chat_id)
        if lf is None or head <= lf:
            self.last_forwarded[chat_id] = max(lf or 0, head)
            return
        items, _ = await run_db(store.messages_after, chat_id, lf, CATCHUP_LIMIT)
        self.stats["repaired"] += len(items)
        logger.info("repair chat=%s %s→%s (%d msgs)", chat_id, lf, head, len(items))
        for m in items:
            await self._on_deliver(chat_topic(chat_id), _msg_event(m, catchup=False, repaired=True), repaired=True)

    async def _on_deliver(self, topic: str, ev: dict, repaired: bool = False):
        if topic.startswith("c:"):
            chat_id = topic[2:]
            mid = ev.get("message_id", 0)
            if not repaired and mid <= self.last_forwarded.get(chat_id, 0):
                # 交接競態：舊 owner 送出的最後幾則還在舊 link 的佇列裡，新 owner 的 head 比對已從 DB 補送過 → 丟掉重複
                self.stats["dedup_dropped"] += 1
                logger.info("dedup_dropped chat=%s mid=%s lf=%s owner=%s", chat_id, mid, self.last_forwarded.get(chat_id), ev.get("owner"))
                return
            self.last_forwarded[chat_id] = max(self.last_forwarded.get(chat_id, 0), mid)
            for c in list(self.chat_conns.get(chat_id, ())):
                if not repaired and mid <= c.floor.get(chat_id, 0):
                    # 先訂閱再補收的重疊：補收直接讀 DB 已經送過這則，live 那份晚到 → 不重送
                    self.stats["catchup_overlap_skip"] += 1
                    continue
                c.floor[chat_id] = max(c.floor.get(chat_id, 0), mid)
                c.send(ev)
            if repaired or self._trace:
                logger.info("fwd chat=%s mid=%s repaired=%s owner=%s conns=%d", chat_id, mid, repaired, ev.get("owner"),
                            len(self.chat_conns.get(chat_id, ())))
            self.stats["delivered"] += len(self.chat_conns.get(chat_id, ()))
        elif topic.startswith("u:"):
            user = topic[2:]
            chat_id = ev.get("chat_id")
            for c in list(self.conns.get(user, ())):
                if ev.get("type") == "chatAdded" and chat_id not in c.chats:
                    self._join(c, chat_id)
                elif ev.get("type") == "chatRemoved" and chat_id in c.chats:
                    self._leave(c, chat_id)
                c.send(ev)

    def _join(self, c: ClientConn, chat_id: str):
        c.chats.add(chat_id)
        self.chat_conns[chat_id].add(c)
        self._ref(chat_topic(chat_id), +1)

    def _leave(self, c: ClientConn, chat_id: str):
        c.chats.discard(chat_id)
        self.chat_conns[chat_id].discard(c)
        if not self.chat_conns[chat_id]:
            del self.chat_conns[chat_id]
        self._ref(chat_topic(chat_id), -1)

    # ------------------------------------------------------------ routing with retry
    async def call_owner(self, topic: str, frame: dict) -> dict:
        """把請求送到 topic 的 owner；owner 不在 / 搬家中就等 shardmap 變更後重試（同一個 req 內容）。"""
        deadline = time.monotonic() + PUBLISH_DEADLINE_S
        backoff, store_retry = 0.2, 0
        last = "UNAVAILABLE"
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.stats["publish_deadline"] += 1
                return {"type": "publish_failed", "code": "UNAVAILABLE", "detail": f"owner 無回應（last={last}）"}
            o = self.map.owner_of_topic(topic)
            link = self.links.get(o.node_id) if o else None
            if not link or not link.connected:
                last = "NO_OWNER" if not o else "LINK_DOWN"
                await self.map.wait_change(min(1.0, remain))
                continue
            try:
                frame["req_id"] = uuid.uuid4().hex
                resp = await link.request(frame, min(PUBLISH_ATTEMPT_TIMEOUT_S, remain))
            except (LinkClosed, asyncio.TimeoutError) as e:
                last = type(e).__name__
                self.stats["publish_retry"] += 1
                await asyncio.sleep(min(backoff, max(0, remain)))
                backoff = min(1.0, backoff * 2)
                continue
            if resp.get("type") != "publish_failed":
                return resp
            code = resp.get("code")
            if code in RETRYABLE or (code == "STORE_ERROR" and store_retry == 0):
                store_retry += code == "STORE_ERROR"
                last = code
                self.stats["publish_retry"] += 1
                if self.view and code == "NOT_OWNER":
                    self.view.refresh()
                await asyncio.sleep(min(backoff, max(0, remain)))
                backoff = min(1.0, backoff * 2)
                continue
            return resp

    async def _notify_user(self, user_id: str, event: dict):
        await self.call_owner(user_topic(user_id), {"type": "notify", "topic": user_topic(user_id), "event": event})

    # ------------------------------------------------------------ client session
    async def serve(self, ws, user_id: str):
        c = ClientConn(ws, user_id)
        self.conns[user_id].add(c)
        self.stats["connections"] += 1
        try:
            pairs = await run_db(store.list_user_chats, user_id)
            chats = await self._chat_summaries(pairs)
            c.send({"type": "welcome", "gateway_id": self.node_id, "user_id": user_id,
                    "server_ts": int(time.time() * 1000), "chats": chats})
            self._ref(user_topic(user_id), +1)
            for chat_id, _ in pairs:
                self._join(c, chat_id)
            await self.reconcile()          # 先訂閱 …
            for chat_id, last_read in pairs:  # … 再補收（離線期間的訊息）
                await self._catchup(c, chat_id, last_read)
            async for raw in ws.iter_text():
                try:
                    f = json.loads(raw)
                except ValueError:
                    c.send({"type": "error", "code": "BAD_FRAME", "detail": "invalid JSON"})
                    continue
                asyncio.create_task(self._dispatch(c, f))
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError"):
                logger.warning("client %s session error: %s", user_id, e)
        finally:
            c.close()
            self.conns[user_id].discard(c)
            if not self.conns[user_id]:
                del self.conns[user_id]
            for chat_id in list(c.chats):
                self._leave(c, chat_id)
            self._ref(user_topic(user_id), -1)

    async def _chat_summaries(self, pairs) -> list[dict]:
        ids = [cid for cid, _ in pairs]
        infos = await run_db(store.get_chats, ids)

        async def one(cid, lr):
            members, head = await asyncio.gather(run_db(store.list_members, cid), run_db(store.max_message_id, cid))
            return {"chat_id": cid, "name": infos.get(cid, {}).get("name"), "participants": members,
                    "last_read": lr, "head": head}
        return list(await asyncio.gather(*(one(cid, lr) for cid, lr in pairs)))

    async def _catchup(self, c: ClientConn, chat_id: str, after: int):
        items, more = await run_db(store.messages_after, chat_id, after, CATCHUP_LIMIT)
        for m in items:
            c.send(_msg_event(m, catchup=True))
        if items:
            c.floor[chat_id] = max(c.floor.get(chat_id, 0), items[-1]["message_id"])
        c.send({"type": "catchupDone", "chat_id": chat_id, "count": len(items), "has_more": more})
        self.stats["catchup_msgs"] += len(items)

    async def _dispatch(self, c: ClientConn, f: dict):
        t, rid = f.get("type"), f.get("req_id")
        try:
            if t == "sendMessage":
                await self._send_message(c, f)
            elif t == "createChat":
                await self._create_chat(c, f)
            elif t == "modifyChatParticipants":
                await self._modify(c, f)
            elif t == "fetchMessages":
                await self._fetch(c, f)
            elif t == "listChats":
                pairs = await run_db(store.list_user_chats, c.user_id)
                c.send({"type": "chats", "req_id": rid, "chats": await self._chat_summaries(pairs)})
            elif t == "ack":
                key = (c.user_id, f["chat_id"])
                self.ack_buf[key] = max(self.ack_buf.get(key, 0), int(f["message_id"]))
            elif t == "ping":
                c.send({"type": "pong", "t": f.get("t")})
            else:
                c.send({"type": "error", "req_id": rid, "code": "BAD_FRAME", "detail": f"unknown type {t!r}"})
        except (KeyError, TypeError, ValueError) as e:
            c.send({"type": "error", "req_id": rid, "code": "BAD_FRAME", "detail": f"欄位錯誤: {e}"})
        except StoreError as e:
            c.send(_result(rid, t, "FAILURE", error="STORE_ERROR", detail=str(e)))

    async def _send_message(self, c: ClientConn, f: dict):
        rid, chat_id, text = f.get("req_id"), f["chat_id"], str(f.get("text", ""))
        cmid = f.get("client_msg_id") or uuid.uuid4().hex
        if len(text.encode()) > MAX_TEXT_BYTES:
            return c.send(_result(rid, "sendMessage", "FAILURE", chat_id=chat_id, client_msg_id=cmid,
                                  error="PAYLOAD_TOO_LARGE"))
        async with c.send_locks[chat_id]:  # 同一條連線、同一個 chat：照送出順序指派
            resp = await self.call_owner(chat_topic(chat_id), {
                "type": "publish", "chat_id": chat_id, "client_msg_id": cmid, "sender": c.user_id,
                "text": text, "client_ts": f.get("client_ts")})
        if resp.get("type") == "published":
            c.send(_result(rid, "sendMessage", "SUCCESS", chat_id=chat_id, client_msg_id=cmid,
                           message_id=resp["message_id"], created_at=resp.get("created_at"), dup=resp.get("dup")))
        else:
            logger.warning("publish_failed chat=%s user=%s code=%s detail=%s", chat_id, c.user_id, resp.get("code"),
                           resp.get("detail"))  # CloudWatch metric filter 依此計數
            c.send(_result(rid, "sendMessage", "FAILURE", chat_id=chat_id, client_msg_id=cmid,
                           error=resp.get("code"), detail=resp.get("detail")))

    async def _create_chat(self, c: ClientConn, f: dict):
        rid, name = f.get("req_id"), str(f.get("name") or "")[:80]
        raw = [str(u).strip() for u in f.get("participants", []) if str(u).strip()]
        bad = [u for u in raw if not USER_RE.match(u)]
        if bad:
            return c.send(_result(rid, "createChat", "FAILURE", error="BAD_FRAME", detail=f"非法 user_id: {bad[:3]}"))
        people = list(dict.fromkeys([c.user_id] + raw))
        if len(people) > MAX_PARTICIPANTS:
            return c.send(_result(rid, "createChat", "FAILURE", error="TOO_MANY_PARTICIPANTS",
                                  detail=f"{len(people)} > {MAX_PARTICIPANTS}"))
        chat_id = "c_" + uuid.uuid4().hex[:12]
        await run_db(store.create_chat, chat_id, name or ", ".join(people[:3]), c.user_id, people)
        ev = {"type": "chatAdded", "chat_id": chat_id, "name": name or ", ".join(people[:3]), "participants": people,
              "last_read": 0, "head": 0}
        if chat_id not in c.chats:  # 建立者這條連線立刻加入（不只靠 u: 通知，通知是 best-effort）
            self._join(c, chat_id)
        c.send(_result(rid, "createChat", "SUCCESS", chat_id=chat_id, chat=ev))
        await asyncio.gather(*(self._notify_user(u, ev) for u in people))

    async def _modify(self, c: ClientConn, f: dict):
        rid, chat_id, target, op = f.get("req_id"), f["chat_id"], str(f["user_id"]).strip(), f["operation"]
        if not USER_RE.match(target) or op not in ("ADD", "REMOVE"):
            return c.send(_result(rid, "modifyChatParticipants", "FAILURE", error="BAD_FRAME"))
        resp = await self.call_owner(chat_topic(chat_id), {"type": "modify", "chat_id": chat_id, "actor": c.user_id,
                                                           "user_id": target, "operation": op})
        if resp.get("type") != "modified":
            return c.send(_result(rid, "modifyChatParticipants", "FAILURE", chat_id=chat_id,
                                  error=resp.get("code"), detail=resp.get("detail")))
        c.send(_result(rid, "modifyChatParticipants", "SUCCESS", chat_id=chat_id, message_id=resp.get("message_id")))
        if resp.get("noop"):
            return
        chat = resp.get("chat", {})
        if op == "ADD":
            last_read = await run_db(store.get_last_read, target, chat_id)
            await self._notify_user(target, {"type": "chatAdded", **chat, "last_read": last_read,
                                             "head": resp.get("message_id")})
        else:
            await self._notify_user(target, {"type": "chatRemoved", "chat_id": chat_id})

    async def _fetch(self, c: ClientConn, f: dict):
        rid, chat_id = f.get("req_id"), f["chat_id"]
        if chat_id not in c.chats:
            return c.send({"type": "error", "req_id": rid, "code": "NOT_MEMBER", "detail": chat_id})
        limit = max(1, min(int(f.get("limit", CATCHUP_LIMIT)), CATCHUP_LIMIT))
        items, more = await run_db(store.messages_after, chat_id, int(f.get("after_id", 0)), limit)
        c.send({"type": "messages", "req_id": rid, "chat_id": chat_id,
                "items": [_msg_event(m, catchup=True) for m in items], "has_more": more})
        self.stats["fetch_msgs"] += len(items)

    # ------------------------------------------------------------ ack flush
    async def _ack_flush_loop(self):
        while True:
            await asyncio.sleep(ACK_FLUSH_MS / 1000)
            await self._flush_acks()

    async def _flush_acks(self):
        buf, self.ack_buf = self.ack_buf, {}
        for (user, chat), mid in buf.items():
            try:
                await run_db(store.update_last_read, user, chat, mid)
            except Exception as e:  # noqa: BLE001
                logger.warning("ack flush failed %s/%s: %s", user, chat, e)

    # ------------------------------------------------------------ introspection
    def cluster(self) -> dict:
        snap = self.map.snapshot()
        snap.update({
            "gateway_id": self.node_id,
            "etcd_connected": self.view.connected if self.view else None,
            "links": {nid: link.connected for nid, link in self.links.items()},
            "topics_wanted": len(self.refs),
            "topics_subscribed": len(self.sub_at),
            "connections": sum(len(s) for s in self.conns.values()),
            "users": len(self.conns),
            "stats": dict(self.stats),
        })
        return snap


def _msg_event(m: dict, catchup: bool, repaired: bool = False) -> dict:
    return {"type": "newMessage", "chat_id": m["chat_id"], "message_id": m["message_id"], "kind": m.get("kind", "user"),
            "text": m.get("text"), "created_by": m.get("created_by"), "created_at": m.get("created_at"),
            "client_msg_id": m.get("client_msg_id"), "client_ts": m.get("client_ts"), "catchup": catchup,
            **({"repaired": True} if repaired else {})}


def _result(rid, op, status, **kw) -> dict:
    return {"type": "result", "req_id": rid, "op": op, "status": status, **{k: v for k, v in kw.items() if v is not None}}
