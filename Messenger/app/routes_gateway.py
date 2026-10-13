"""gateway 的對外端點：/ws（client WebSocket）+ demo 頁 + 維運 API。"""
import os

from fastapi import APIRouter, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .auth import user_from_ws
from .config import ALLOW_DEBUG, STATIC_DIR, public_meta


class KillRequest(BaseModel):
    node_id: str
    mode: str = "graceful"  # graceful | hard


def build_router(gw, pubsub_node=None) -> APIRouter:
    r = APIRouter()

    @r.websocket("/ws")
    async def client_ws(ws: WebSocket):
        user_id = user_from_ws(ws)
        if not user_id:
            await ws.close(code=4401)  # 未授權 / user_id 不合法
            return
        await ws.accept()
        await gw.serve(ws, user_id)

    @r.get("/api/config/meta")
    def meta():
        return {**public_meta(), "gateway_id": gw.node_id}

    @r.get("/api/cluster")
    def cluster():
        data = gw.cluster()
        if pubsub_node is not None:  # 單 process：順便附上 owner 狀態
            data["pubsub"] = {pubsub_node.info.node_id: pubsub_node.state()}
        return data

    @r.post("/api/debug/kill")
    async def kill(req: KillRequest):
        if not ALLOW_DEBUG:
            return JSONResponse(status_code=404, content={"detail": "ALLOW_DEBUG=0"})
        node = gw.map.nodes.get(req.node_id)
        if not node or pubsub_node is not None:
            return JSONResponse(status_code=400, content={"detail": "只有 cluster 模式能 kill pub/sub 節點"})
        import httpx
        async with httpx.AsyncClient(timeout=3) as c:
            resp = await c.post(f"{node.http}/debug/exit", params={"mode": req.mode})
        return {"requested": req.node_id, "mode": req.mode, "pubsub_response": resp.json()}

    @r.get("/health")
    def health():
        return {"status": "ok", "role": "gateway", "node_id": gw.node_id}

    @r.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return r
