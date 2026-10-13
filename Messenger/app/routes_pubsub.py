"""pub/sub 節點的端點（只給內網的 gateway 連，不對外）。"""
import asyncio
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .config import ALLOW_DEBUG
from .errors import logger
from .links import QueuePeer


def build_router(node) -> APIRouter:
    r = APIRouter()

    @r.websocket("/internal/ws")
    async def internal_ws(ws: WebSocket):
        await ws.accept()
        gid = ws.query_params.get("gateway_id", "gw?")
        peer = QueuePeer(gid, lambda f: ws.send_text(json.dumps(f, ensure_ascii=False)))
        logger.info("gateway %s connected", gid)

        async def handle(f):
            resp = await node.handle(peer, f)
            if resp is not None:
                peer.send(resp)

        try:
            while not peer.closed.is_set():
                recv = asyncio.create_task(ws.receive_text())
                closed = asyncio.create_task(peer.closed.wait())
                done, _ = await asyncio.wait({recv, closed}, return_when=asyncio.FIRST_COMPLETED)
                if recv not in done:
                    recv.cancel()
                    break  # 慢消費者被踢：關閉連線，gateway 會重連並補送
                closed.cancel()
                asyncio.create_task(handle(json.loads(recv.result())))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            node.on_peer_closed(peer)
            peer.close()
            try:
                await ws.close(code=1013)
            except Exception:  # noqa: BLE001
                pass
            logger.info("gateway %s disconnected", gid)

    @r.get("/debug/state")
    def debug_state():
        return node.state()

    @r.post("/debug/exit")
    async def debug_exit(mode: str = "graceful"):
        """demo 用：graceful = revoke lease 後結束（shard 立刻轉移）；hard = 直接 os._exit（等 lease TTL 過期）。"""
        if not ALLOW_DEBUG:
            return JSONResponse(status_code=404, content={"detail": "ALLOW_DEBUG=0"})

        async def later():
            await asyncio.sleep(0.2)
            if mode == "graceful":
                await node.shutdown()
            logger.warning("debug exit (%s)", mode)
            os._exit(0 if mode == "graceful" else 1)
        asyncio.create_task(later())
        return {"node_id": node.info.node_id, "mode": mode, "exiting": True}

    @r.get("/health")
    def health():
        return {"status": "ok", "role": "pubsub", "node_id": node.info.node_id, "owned": len(node.owned)}

    return r
