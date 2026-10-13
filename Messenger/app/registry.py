"""Service discovery（PDF 深入探討 1：shard_id → pub/sub server 對應 + heartbeat）。

技術選型定案：**etcd**（Raft 強一致、lease + txn + watch 原生）。client 不裝 grpcio，直接用 httpx 打
etcd v3 的 gRPC-gateway JSON API（同一個 2379 port，/v3/...）。要注意的細節：
- key / value 都是 base64
- int64 欄位（revision、lease ID）在 JSON 裡是字串
- watch 是長連線，一行一個 {"result": {...}}；斷線要從 revision+1 續看，否則會漏事件
- 欄位名可能是 snake_case 或 camelCase（依 etcd 版本），_g() 兩種都讀

key 佈局（ETCD_PREFIX 預設 /messenger/）：
  nodes/{node_id}   → {"ws","http"}        附 lease：節點活著才存在
  shards/{0000..}   → {"node_id","ws","http"} 附 owner 的 lease：owner 掛掉 lease 過期 → key 自動刪除

元件：
- EtcdClient   薄薄一層 HTTP 包裝
- ClusterView  把 nodes/ + shards/ 同步進本地 ShardMap（gateway 與 pubsub 都用）
- EtcdMember   pubsub 專用：lease、keepalive、claim / release / verify（fencing 第一層）
- 單 process 模式不用 etcd：static_cluster() 讓本節點持有全部 shard
"""
import asyncio
import base64
import json
import time

from .config import ETCD_PREFIX, ETCD_URL, LEASE_TTL_S, KEEPALIVE_EVERY_S, NUM_SHARDS
from .errors import logger
from .shardmap import NodeInfo, Owner, ShardMap


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _unb64(s: str | None) -> str:
    return base64.b64decode(s).decode() if s else ""


def _prefix_end(prefix: str) -> str:
    b = bytearray(prefix.encode())
    b[-1] += 1
    return base64.b64encode(bytes(b)).decode()


def _g(d: dict, snake: str, camel: str | None = None, default=None):
    if snake in d:
        return d[snake]
    if camel and camel in d:
        return d[camel]
    return default


def shard_key(i: int) -> str:
    return f"{ETCD_PREFIX}shards/{i:04d}"


def node_key(node_id: str) -> str:
    return f"{ETCD_PREFIX}nodes/{node_id}"


class EtcdClient:
    def __init__(self, url: str):
        import httpx  # 延遲匯入
        self._httpx = httpx
        self._c = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=httpx.Timeout(5.0, read=5.0))
        self.url = url

    async def _post(self, path: str, body: dict) -> dict:
        r = await self._c.post(path, json=body)
        r.raise_for_status()
        return r.json()

    async def grant(self, ttl: int) -> str:
        return str((await self._post("/v3/lease/grant", {"TTL": ttl}))["ID"])

    async def keepalive(self, lease: str) -> int:
        resp = await self._post("/v3/lease/keepalive", {"ID": lease})
        res = resp.get("result", resp)
        return int(res.get("TTL", 0) or 0)  # 0 / 缺欄位 = lease 已過期

    async def revoke(self, lease: str) -> None:
        await self._post("/v3/lease/revoke", {"ID": lease})

    async def put(self, key: str, value: str, lease: str | None = None) -> None:
        body = {"key": _b64(key), "value": _b64(value)}
        if lease:
            body["lease"] = lease
        await self._post("/v3/kv/put", body)

    async def get(self, key: str) -> dict | None:
        kvs = (await self._post("/v3/kv/range", {"key": _b64(key)})).get("kvs", [])
        return self._kv(kvs[0]) if kvs else None

    async def range_prefix(self, prefix: str) -> tuple[list[dict], int]:
        resp = await self._post("/v3/kv/range", {"key": _b64(prefix), "range_end": _prefix_end(prefix)})
        rev = int(_g(resp.get("header", {}), "revision", default=0))
        return [self._kv(kv) for kv in resp.get("kvs", [])], rev

    async def create_if_absent(self, key: str, value: str, lease: str) -> bool:
        """txn: key 不存在（create_revision == 0）才 put。這就是 shard claim 的 CAS。"""
        resp = await self._post("/v3/kv/txn", {
            "compare": [{"key": _b64(key), "result": "EQUAL", "target": "CREATE", "create_revision": "0"}],
            "success": [{"request_put": {"key": _b64(key), "value": _b64(value), "lease": lease}}],
        })
        return bool(resp.get("succeeded"))

    async def delete_if_value(self, key: str, value: str) -> bool:
        resp = await self._post("/v3/kv/txn", {
            "compare": [{"key": _b64(key), "result": "EQUAL", "target": "VALUE", "value": _b64(value)}],
            "success": [{"request_delete_range": {"key": _b64(key)}}],
        })
        return bool(resp.get("succeeded"))

    async def watch(self, prefix: str, start_revision: int):
        """async generator：yield (type, kv)。type = PUT | DELETE。"""
        body = {"create_request": {"key": _b64(prefix), "range_end": _prefix_end(prefix),
                                   "start_revision": str(start_revision)}}
        timeout = self._httpx.Timeout(5.0, read=None)
        async with self._c.stream("POST", "/v3/watch", json=body, timeout=timeout) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                res = json.loads(line).get("result", {})
                for ev in res.get("events", []) or []:
                    yield ev.get("type", "PUT"), self._kv(ev.get("kv", {}))

    @staticmethod
    def _kv(kv: dict) -> dict:
        return {"key": _unb64(kv.get("key")), "value": _unb64(kv.get("value")),
                "mod_revision": int(_g(kv, "mod_revision", "modRevision", 0)),
                "create_revision": int(_g(kv, "create_revision", "createRevision", 0)),
                "lease": str(_g(kv, "lease", default="") or "")}

    async def aclose(self):
        await self._c.aclose()


def _parse(kv: dict):
    """etcd kv → ('shard', i, Owner) | ('node', id, NodeInfo)"""
    rel = kv["key"][len(ETCD_PREFIX):]
    try:
        v = json.loads(kv["value"]) if kv["value"] else {}
    except ValueError:
        return None
    if rel.startswith("shards/"):
        return "shard", int(rel.split("/", 1)[1]), Owner(v.get("node_id", "?"), v.get("ws", ""), v.get("http", ""),
                                                          kv["mod_revision"])
    if rel.startswith("nodes/"):
        nid = rel.split("/", 1)[1]
        return "node", nid, NodeInfo(nid, v.get("ws", ""), v.get("http", ""), kv.get("lease", ""))
    return None


class ClusterView:
    """把 etcd 的 nodes/ 與 shards/ 同步進本地 ShardMap：先 range 整批，再從 revision+1 watch。"""

    def __init__(self, etcd: EtcdClient, shardmap: ShardMap):
        self.etcd, self.map = etcd, shardmap
        self.connected = False
        self._task: asyncio.Task | None = None
        self._kick = asyncio.Event()

    def start(self):
        self._task = asyncio.create_task(self._run())

    def refresh(self):
        """外部提示（pubsub 送 shard_moved）→ 立刻重建，不等 watch。"""
        self._kick.set()

    async def load(self) -> int:
        kvs, rev = await self.etcd.range_prefix(ETCD_PREFIX)
        owners, nodes = {}, {}
        for kv in kvs:
            p = _parse(kv)
            if not p:
                continue
            if p[0] == "shard":
                owners[p[1]] = p[2]
            else:
                nodes[p[1]] = p[2]
        self.map.replace(owners, nodes, rev)
        return rev

    async def _run(self):
        while True:
            try:
                rev = await self.load()
                self.connected = True
                watch = asyncio.create_task(self._watch(rev + 1))
                kick = asyncio.create_task(self._kick.wait())
                done, _ = await asyncio.wait({watch, kick}, return_when=asyncio.FIRST_COMPLETED)
                for t in (watch, kick):
                    if not t.done():
                        t.cancel()
                self._kick.clear()
                if watch in done and watch.exception():
                    raise watch.exception()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                logger.warning("cluster view: etcd error (%s), retry in 1s", e)
                await asyncio.sleep(1)

    async def _watch(self, start_rev: int):
        async for typ, kv in self.etcd.watch(ETCD_PREFIX, start_rev):
            p = _parse(kv) if typ != "DELETE" else None
            rel = kv["key"][len(ETCD_PREFIX):]
            if rel.startswith("shards/"):
                i = int(rel.split("/", 1)[1])
                self.map.set_owner(i, p[2] if p else None, kv["mod_revision"])
            elif rel.startswith("nodes/"):
                nid = rel.split("/", 1)[1]
                self.map.set_node(p[2] if p else None, nid, kv["mod_revision"])

    async def stop(self):
        if self._task:
            self._task.cancel()


class EtcdMember:
    """pubsub 節點在 etcd 的身分：lease + keepalive + shard claim/release。

    自我 fencing：最後一次成功 keepalive 超過 TTL-1s → lease_healthy() = False → 拒絕 publish。
    （此時 etcd 可能已經讓 lease 過期、別人可能已接手；寧可暫時不服務也不要雙主寫入。）
    """

    def __init__(self, etcd: EtcdClient, info: NodeInfo, on_lease_lost):
        self.etcd, self.info = etcd, info
        self.lease: str | None = None
        self.last_ok = 0.0
        self._on_lease_lost = on_lease_lost
        self._task: asyncio.Task | None = None
        self._stopping = False

    def value(self) -> str:
        return json.dumps({"node_id": self.info.node_id, "ws": self.info.ws, "http": self.info.http},
                          separators=(",", ":"))

    async def start(self):
        await self._acquire()
        self._task = asyncio.create_task(self._keepalive_loop())

    async def _acquire(self):
        self.lease = await self.etcd.grant(LEASE_TTL_S)
        await self.etcd.put(node_key(self.info.node_id), self.value(), self.lease)
        self.last_ok = time.monotonic()
        logger.info("etcd: node %s registered (lease %s, ttl %ss)", self.info.node_id, self.lease, LEASE_TTL_S)

    def lease_healthy(self) -> bool:
        return bool(self.lease) and (time.monotonic() - self.last_ok) < (LEASE_TTL_S - 1)

    async def _keepalive_loop(self):
        while not self._stopping:
            await asyncio.sleep(KEEPALIVE_EVERY_S)
            try:
                ttl = await self.etcd.keepalive(self.lease)
                if ttl > 0:
                    self.last_ok = time.monotonic()
                    continue
                logger.error("etcd: lease %s expired → 放棄所有 shard、重新註冊", self.lease)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("etcd keepalive failed: %s", e)
                if self.lease_healthy():
                    continue  # 還在寬限內，下輪再試
            # lease 失效或長時間連不上：交出 shard，重新拿 lease
            await self._on_lease_lost()
            try:
                await self._acquire()
            except Exception as e:  # noqa: BLE001
                logger.warning("etcd re-register failed: %s", e)

    async def claim(self, shard: int) -> bool:
        if not self.lease_healthy():
            return False
        return await self.etcd.create_if_absent(shard_key(shard), self.value(), self.lease)

    async def release(self, shard: int) -> bool:
        return await self.etcd.delete_if_value(shard_key(shard), self.value())

    async def verify_owner(self, shard: int) -> bool:
        kv = await self.etcd.get(shard_key(shard))
        if not kv:
            return False
        try:
            return json.loads(kv["value"]).get("node_id") == self.info.node_id
        except ValueError:
            return False

    async def revoke(self):
        """graceful 下線：revoke lease → 所有 shard key 立刻消失 → 別人立刻接手（不用等 TTL）。"""
        self._stopping = True
        if self._task:
            self._task.cancel()
        if self.lease:
            try:
                await self.etcd.revoke(self.lease)
                logger.info("etcd: lease %s revoked", self.lease)
            except Exception as e:  # noqa: BLE001
                logger.warning("etcd revoke failed: %s", e)
            self.lease = None


def static_cluster(info: NodeInfo) -> ShardMap:
    """單 process 模式：本節點持有全部 shard，不需要 etcd。"""
    m = ShardMap(num_shards=NUM_SHARDS)
    m.replace({i: Owner(info.node_id, info.ws, info.http) for i in range(NUM_SHARDS)},
              {info.node_id: info}, 1)
    return m


def etcd_client() -> EtcdClient | None:
    return EtcdClient(ETCD_URL) if ETCD_URL else None
