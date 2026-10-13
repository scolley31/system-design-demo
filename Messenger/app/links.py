"""gateway → pub/sub 的連線（技術選型定案：內部 WebSocket；單 process 模式用直接呼叫）。

兩種實作、同一個介面：
- LocalLink  ROLE=all：直接呼叫同 process 的 PubSubNode（沒有 socket，但邏輯一模一樣）
- WsLink     ROLE=gateway：對每個 pub/sub 節點維持一條長連線；斷線自動重連（0.2s→2s backoff）。
             request/response 用 req_id 對應；deliver / shard_moved 等主動推送交給 gateway.on_link_frame。

pub/sub 端看到的對應物是 Peer（LocalPeer / WsPeer，見 routes_pubsub.py）。
"""
import asyncio
import json
import uuid

from .config import NODE_ID, SEND_QUEUE_MAX
from .errors import logger
from .pubsub import Peer, PubSubNode


class LinkClosed(Exception):
    pass


class QueuePeer(Peer):
    """有上限的送出佇列 + 一個 writer task。WsPeer / LocalPeer 共用。"""

    def __init__(self, peer_id: str, writer):
        self.peer_id = peer_id
        self.q: asyncio.Queue = asyncio.Queue(SEND_QUEUE_MAX)
        self._writer = writer
        self._task = asyncio.create_task(self._drain())
        self.closed = asyncio.Event()

    def send(self, frame: dict) -> bool:
        if self.closed.is_set():
            return False
        try:
            self.q.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            return False

    async def _drain(self):
        try:
            while True:
                await self._writer(await self.q.get())
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.info("peer %s writer stopped: %s", self.peer_id, e)
        finally:
            self.closed.set()

    def close(self) -> None:
        self.closed.set()
        self._task.cancel()


class LocalLink:
    def __init__(self, node_id: str, node: PubSubNode, gateway):
        self.node_id, self.node, self.gw = node_id, node, gateway
        self.connected = True
        self.peer = QueuePeer(f"{NODE_ID}-local", self._deliver)

    async def _deliver(self, frame: dict):
        await self.gw.on_link_frame(self.node_id, frame)

    async def request(self, frame: dict, timeout: float) -> dict:
        return await asyncio.wait_for(self.node.handle(self.peer, frame), timeout) or {}

    def send(self, frame: dict) -> None:
        asyncio.create_task(self.node.handle(self.peer, frame))

    async def close(self):
        self.connected = False


class WsLink:
    def __init__(self, node_id: str, url: str, gateway):
        self.node_id, self.url, self.gw = node_id, url, gateway
        self.connected = False
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._stop = False
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        import websockets  # 延遲匯入
        backoff = 0.2
        while not self._stop:
            try:
                async with websockets.connect(f"{self.url}?gateway_id={NODE_ID}", open_timeout=3,
                                              ping_interval=10, ping_timeout=10, max_queue=None) as ws:
                    self._ws, self.connected, backoff = ws, True, 0.2
                    logger.info("link up → %s (%s)", self.node_id, self.url)
                    await self.gw.on_link_up(self.node_id)
                    async for raw in ws:
                        f = json.loads(raw)
                        fut = self._pending.pop(f.get("req_id") or "", None)
                        if fut is not None:
                            if not fut.done():
                                fut.set_result(f)
                        else:
                            await self.gw.on_link_frame(self.node_id, f)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                if self.connected or backoff <= 0.2:
                    logger.warning("link %s down: %s", self.node_id, e)
            finally:
                was = self.connected
                self.connected, self._ws = False, None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(LinkClosed(self.node_id))
                self._pending.clear()
                if was:
                    await self.gw.on_link_down(self.node_id)
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(2.0, backoff * 2)

    async def request(self, frame: dict, timeout: float) -> dict:
        ws = self._ws
        if not self.connected or ws is None:
            raise LinkClosed(self.node_id)
        rid = frame.setdefault("req_id", uuid.uuid4().hex)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await ws.send(json.dumps(frame))
            return await asyncio.wait_for(fut, timeout)
        except Exception as e:
            self._pending.pop(rid, None)
            if isinstance(e, (asyncio.TimeoutError, LinkClosed)):
                raise
            raise LinkClosed(f"{self.node_id}: {e}") from e

    def send(self, frame: dict) -> None:
        ws = self._ws
        if self.connected and ws is not None:
            asyncio.create_task(ws.send(json.dumps(frame)))

    async def close(self):
        self._stop = True
        self._task.cancel()
