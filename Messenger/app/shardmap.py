"""shard 計算 + shard → owner 對應快取（PDF 深入探討 1：Pub/sub model based on chat）。

- shard_id = crc32(topic) % S。**不能用 Python 內建 hash()**：字串 hash 每個 process 隨機化
  （PYTHONHASHSEED），兩個 gateway 會算出不同 shard，路由直接錯亂。
- topic 兩種：
    c:{chat_id}  一個 chat 的訊息流（有序、持久）——由 owner 指派 message_id
    u:{user_id}  使用者的控制事件（chatAdded / chatRemoved，不持久）
- ShardMap 是 gateway / pubsub 對 etcd 內容的本地快取；變更時喚醒等待者並呼叫 callback。
"""
import asyncio
import zlib
from dataclasses import dataclass, field

from .config import NUM_SHARDS


def shard_of(topic: str, num_shards: int = NUM_SHARDS) -> int:
    return zlib.crc32(topic.encode("utf-8")) % num_shards


def chat_topic(chat_id: str) -> str:
    return f"c:{chat_id}"


def user_topic(user_id: str) -> str:
    return f"u:{user_id}"


@dataclass(frozen=True)
class Owner:
    node_id: str
    ws: str
    http: str
    mod_revision: int = 0


@dataclass
class NodeInfo:
    node_id: str
    ws: str
    http: str
    lease: str = ""


@dataclass
class ShardMap:
    num_shards: int = NUM_SHARDS
    owners: dict[int, Owner] = field(default_factory=dict)
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    revision: int = 0
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    _callbacks: list = field(default_factory=list)
    history: list = field(default_factory=list)  # demo：shard 搬家紀錄（最近 50 筆）

    def owner(self, shard: int) -> Owner | None:
        return self.owners.get(shard)

    def owner_of_topic(self, topic: str) -> Owner | None:
        return self.owners.get(shard_of(topic, self.num_shards))

    def on_change(self, cb) -> None:
        self._callbacks.append(cb)

    async def wait_change(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def replace(self, owners: dict[int, Owner], nodes: dict[str, NodeInfo], revision: int) -> None:
        """整批替換（初次載入 / watch 重建）。"""
        changed = self._diff(owners)
        self.owners = dict(owners)
        self.nodes = dict(nodes)
        self.revision = max(self.revision, revision)
        self._notify(changed)

    def set_owner(self, shard: int, owner: Owner | None, revision: int) -> None:
        new = dict(self.owners)
        if owner is None:
            new.pop(shard, None)
        else:
            new[shard] = owner
        changed = self._diff(new)
        self.owners = new
        self.revision = max(self.revision, revision)
        self._notify(changed)

    def set_node(self, node: NodeInfo | None, node_id: str, revision: int) -> None:
        if node is None:
            self.nodes.pop(node_id, None)
        else:
            self.nodes[node_id] = node
        self.revision = max(self.revision, revision)
        self._notify({})

    def _diff(self, new: dict[int, Owner]) -> dict[int, tuple]:
        changed = {}
        for s in set(self.owners) | set(new):
            a, b = self.owners.get(s), new.get(s)
            if (a.node_id if a else None) != (b.node_id if b else None):
                changed[s] = (a, b)
        return changed

    def _notify(self, changed: dict[int, tuple]) -> None:
        import time
        for s, (a, b) in changed.items():
            self.history.append({"t": time.time(), "shard": s,
                                 "from": a.node_id if a else None, "to": b.node_id if b else None})
        del self.history[:-50]
        ev, self._event = self._event, asyncio.Event()
        ev.set()
        for cb in self._callbacks:
            try:
                cb(changed)
            except Exception:  # noqa: BLE001
                pass

    def snapshot(self) -> dict:
        return {
            "num_shards": self.num_shards,
            "revision": self.revision,
            "shards": [{"shard": s, "node_id": (self.owners[s].node_id if s in self.owners else None)}
                       for s in range(self.num_shards)],
            "nodes": [{"node_id": n.node_id, "ws": n.ws, "http": n.http} for n in self.nodes.values()],
            "history": self.history[-20:],
        }
