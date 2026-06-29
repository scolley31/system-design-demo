"""全面 error handling：全域例外處理器、request id、輸入健全化、日誌。

setup_error_handling(app) 一次掛上:
- 未捕捉例外 → 500 {"detail":"Internal server error","request_id":...}（不洩漏內部,記 traceback）
- RequestValidationError → 422 {"detail": <可讀字串>}（含 malformed JSON）
- HTTP middleware：X-Request-ID（取 header 或產生）+ body 大小上限 → 413
錯誤格式沿用 {"detail"} 以相容前端 api() 的 body.detail。
"""
import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 64 * 1024  # 64KB

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("qrcode")


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id.get()
        return True


def _setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_RequestIdFilter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def current_request_id() -> str:
    return _request_id.get()


def setup_error_handling(app: FastAPI) -> None:
    _setup_logging()

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        _request_id.set(rid)
        # 輸入健全化：用 Content-Length 快速擋過大 body
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Payload too large", "request_id": rid},
                        headers={"X-Request-ID": rid},
                    )
            except ValueError:
                pass
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # 把 FastAPI 預設的 list 轉成單一可讀字串（含 malformed JSON 的 JSONDecodeError）
        parts = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", []) if p != "body")
            parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
        msg = "; ".join(parts) or "validation error"
        rid = _request_id.get()
        return JSONResponse(
            status_code=422,
            content={"detail": msg, "request_id": rid},
            headers={"X-Request-ID": rid},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled exception: %s", exc)  # traceback 進日誌,不回給 client
        rid = _request_id.get()
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": rid},
            headers={"X-Request-ID": rid},
        )
