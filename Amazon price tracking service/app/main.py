import asyncio
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .aggregation import run_aggregator
from .cdc import run_tailer
from .config import ROLE
from .database import Base, engine
from .errors import logger, setup_error_handling
from .routes import router
from .scheduler import run_scheduler
from .worker_notify import start_workers

# 原型：啟動時建表。正式版用 migration 工具（如 Alembic）。
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Amazon Price Tracking Service")
setup_error_handling(app)
app.include_router(router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def _startup():
    # 全部背景迴圈在主程序內以 asyncio task 跑：
    #   workers（price-change / senders / crawl）、scheduler（優先式爬取）、CDC tailer、aggregator。
    # 正式版依 ROLE 分流：api 只跑 web；worker ASG 跑 crawler + tailer + workers（見 infra/README）。
    tasks = []
    if ROLE in ("all", "worker"):
        tasks += start_workers()
        tasks += [asyncio.create_task(run_scheduler()), asyncio.create_task(run_tailer()),
                  asyncio.create_task(run_aggregator())]
    app.state.tasks = tasks
    logger.info("startup: role=%s background_tasks=%d", ROLE, len(tasks))


@app.on_event("shutdown")
async def _shutdown():
    for t in getattr(app.state, "tasks", []):
        t.cancel()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    """demo 前端（單一 HTML，零建置）。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
