import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .errors import setup_error_handling
from .gateway import poller_enabled, usgs_poller
from .routes import router
from .worker import start_workers

# 原型：啟動時建表。正式版用 migration 工具（如 Alembic）。
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Earthquake Notification System")
setup_error_handling(app)
app.include_router(router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def _startup():
    # per-channel worker（消費 queue → SSE 送達）在主程序內以 asyncio task 跑。
    app.state.workers = start_workers()
    if poller_enabled():
        app.state.poller = __import__("asyncio").create_task(usgs_poller())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    """demo 前端（單一 HTML，零建置；用 vendored h3-js 在瀏覽器算 cell）。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
