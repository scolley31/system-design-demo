import os

from fastapi import FastAPI
from fastapi.responses import FileResponse

from .database import Base, engine
from .errors import setup_error_handling
from .routes import router

# 原型：啟動時建表。正式版用 migration 工具（如 Alembic）。
Base.metadata.create_all(bind=engine)

app = FastAPI(title="QR Code Generator")
setup_error_handling(app)  # 全域例外處理 + request id + body 限制
app.include_router(router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    """管理頁前端（單一 HTML，零建置）。"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
