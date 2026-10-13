"""端到端冒煙測試（dev 單 process 與 compose cluster 共用）。

  python scripts/smoke_ws.py ws://localhost:8030/ws                         # dev / nginx
  python scripts/smoke_ws.py ws://localhost:8030/gw1/ws ws://localhost:8030/gw2/ws   # alice 連 gw1、bob 連 gw2
  python scripts/smoke_ws.py URL_A URL_B --burst 60 --kill-owner hard        # 送到 1/3 時 SIGKILL 這個 chat 的 owner

驗證：建群 → chatAdded 通知 → 連發 N 則（bob 收到的 id 必須 1..N 連號、無重複）→ bob 離線期間 alice 再送
→ bob 重連補收 → 重送同 client_msg_id 回 dup + 同 id → 成員 ADD 產生系統訊息。
"""
import argparse
import asyncio
import json
import sys
import time
import uuid

import websockets


class Client:
    def __init__(self, url, user):
        self.url, self.user = url, user
        self.inbox: dict[str, list] = {}
        self.events: list = []
        self.results: dict[str, dict] = {}
        self.welcome = None
        self._waiters: dict[str, asyncio.Future] = {}

    async def connect(self):
        sep = "&" if "?" in self.url else "?"
        self.ws = await websockets.connect(f"{self.url}{sep}user_id={self.user}", max_queue=None)
        self.task = asyncio.create_task(self._reader())
        for _ in range(100):
            if self.welcome:
                return self
            await asyncio.sleep(0.05)
        raise RuntimeError(f"{self.user}: no welcome")

    async def _reader(self):
        try:
            async for raw in self.ws:
                f = json.loads(raw)
                t = f.get("type")
                if t == "welcome":
                    self.welcome = f
                elif t == "newMessage":
                    self.inbox.setdefault(f["chat_id"], []).append(f)
                elif t in ("result", "messages", "chats"):
                    fut = self._waiters.pop(f.get("req_id"), None)
                    if fut and not fut.done():
                        fut.set_result(f)
                else:
                    self.events.append(f)
        except websockets.ConnectionClosed:
            pass

    async def call(self, frame, timeout=15):
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._waiters[rid] = fut
        await self.ws.send(json.dumps({**frame, "req_id": rid}))
        return await asyncio.wait_for(fut, timeout)

    async def send(self, chat_id, text, cmid=None):
        return await self.call({"type": "sendMessage", "chat_id": chat_id, "text": text,
                                "client_msg_id": cmid or uuid.uuid4().hex, "client_ts": int(time.time() * 1000)})

    def ids(self, chat_id):
        return [m["message_id"] for m in self.inbox.get(chat_id, [])]

    async def close(self):
        await self.ws.close()
        self.task.cancel()


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        check.failed += 1


check.failed = 0


async def wait_for(pred, timeout=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


async def kill_chat_owner(ws_url: str, chat_id: str, mode: str) -> str:
    """找出 chat 所在 shard 的 owner，透過 gateway 的 /api/debug/kill 殺掉它（測真正的 failover）。"""
    import zlib
    import httpx
    base = ws_url.replace("ws://", "http://").replace("wss://", "https://").rsplit("/ws", 1)[0]
    async with httpx.AsyncClient(timeout=5) as c:
        cl = (await c.get(f"{base}/api/cluster")).json()
        shard = zlib.crc32(f"c:{chat_id}".encode()) % cl["num_shards"]
        owner = next(x["node_id"] for x in cl["shards"] if x["shard"] == shard)
        r = await c.post(f"{base}/api/debug/kill", json={"node_id": owner, "mode": mode})
    return f"💥 kill {owner}（shard {shard} 的 owner，mode={mode}）→ HTTP {r.status_code}"


async def main(a_url, b_url, burst, interval, kill_owner=None):
    sfx = uuid.uuid4().hex[:4]
    alice, bob, carol = f"alice-{sfx}", f"bob-{sfx}", f"carol-{sfx}"
    A = await Client(a_url, alice).connect()
    B = await Client(b_url, bob).connect()
    print(f"alice @ {A.welcome['gateway_id']}, bob @ {B.welcome['gateway_id']}")

    r = await A.call({"type": "createChat", "name": f"smoke-{sfx}", "participants": [bob]})
    chat = r.get("chat_id")
    check(r["status"] == "SUCCESS" and chat, f"createChat → {chat}")
    check(await wait_for(lambda: any(e.get("type") == "chatAdded" and e.get("chat_id") == chat for e in B.events)),
          "bob 收到 chatAdded")

    t0 = time.monotonic()
    fails, lat = [], []
    for i in range(burst):
        if kill_owner and i == burst // 3:
            print("   ", await kill_chat_owner(a_url, chat, kill_owner))
        s = time.monotonic()
        res = await A.send(chat, f"msg {i + 1}")
        lat.append((time.monotonic() - s) * 1000)
        if res["status"] != "SUCCESS":
            fails.append(res)
        if interval:
            await asyncio.sleep(interval)
    check(not fails, f"連發 {burst} 則全部 SUCCESS（{len(fails)} 失敗；{time.monotonic() - t0:.1f}s；"
                     f"ack p50={sorted(lat)[len(lat) // 2]:.0f}ms max={max(lat):.0f}ms）")
    if fails:
        print("    ", fails[:3])
    ok = await wait_for(lambda: len(set(B.ids(chat))) >= burst, 15)
    ids = B.ids(chat)
    uniq = sorted(set(ids))
    check(ok and uniq == list(range(1, burst + 1)), f"bob 收到 1..{burst} 連號（收到 {len(uniq)} 個不同 id）")
    check(ids == sorted(ids), f"bob 收到的順序 = id 順序（重複 {len(ids) - len(uniq)} 則，client 會去重）")

    await B.call({"type": "listChats"})
    await B.ws.send(json.dumps({"type": "ack", "chat_id": chat, "message_id": burst}))
    await asyncio.sleep(1.2)  # 等 gateway 合併 ack 寫 chat_state
    await B.close()

    cm_last = None
    for i in range(5):
        cm_last = uuid.uuid4().hex
        await A.send(chat, f"offline {i + 1}", cmid=cm_last)
    B2 = await Client(b_url, bob).connect()
    got = await wait_for(lambda: len(B2.ids(chat)) >= 5)
    check(got and B2.ids(chat)[:5] == list(range(burst + 1, burst + 6)),
          f"bob 重連補收離線期間 {burst + 1}..{burst + 5}（catchup={all(m['catchup'] for m in B2.inbox.get(chat, []))}）")

    dup = await A.send(chat, "offline 5 (retry)", cmid=cm_last)
    check(dup["status"] == "SUCCESS" and dup.get("dup") and dup.get("message_id") == burst + 5,
          f"重送同 client_msg_id → dup=True、同 message_id {dup.get('message_id')}")

    m = await A.call({"type": "modifyChatParticipants", "chat_id": chat, "user_id": carol, "operation": "ADD"})
    check(m["status"] == "SUCCESS" and m.get("message_id") == burst + 6, f"ADD carol → 系統訊息 #{m.get('message_id')}")
    C = await Client(a_url, carol).connect()
    check(any(c["chat_id"] == chat for c in C.welcome["chats"]), "carol 連上後 welcome 含這個聊天室")
    r3 = await C.send(chat, "hi from carol")
    check(r3["status"] == "SUCCESS" and r3["message_id"] == burst + 7, f"carol 可以發言 → #{r3.get('message_id')}")

    big = await A.call({"type": "createChat", "name": "too-big", "participants": [f"u{i}" for i in range(100)]})
    check(big["status"] == "FAILURE" and big.get("error") == "TOO_MANY_PARTICIPANTS", "101 人建群被拒（上限 100）")

    for c in (A, B2, C):
        await c.close()
    print("PASS" if not check.failed else f"FAIL ({check.failed})")
    return 1 if check.failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("a_url")
    ap.add_argument("b_url", nargs="?")
    ap.add_argument("--burst", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0)
    ap.add_argument("--kill-owner", choices=["hard", "graceful"], help="連發到 1/3 時殺掉這個 chat 的 owner（cluster 模式）")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.a_url, a.b_url or a.a_url, a.burst, a.interval, a.kill_owner)))
