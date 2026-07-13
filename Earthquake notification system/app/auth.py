"""Auth（env-gated）：取得目前使用者。

AUTH_ENABLED 未開（本機預設）→ 回 dev 使用者，不強制登入（本機/原型行為不變）。
開啟 → 驗證 Cognito 簽發的 JWT（id token，RS256），驗 iss/aud/exp/簽章後取 sub。

原型的管理端點目前未強制 auth（demo 以 device_id 識別）；此模組為正式版預留，
掛法比照 QR Code Generator：把 get_current_user 當 FastAPI dependency 注入路由。
"""
import os
from functools import lru_cache

from fastapi import Header, HTTPException

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")
DEV_USER = "local-dev"

COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
ISSUER = (
    f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    if COGNITO_REGION and COGNITO_USER_POOL_ID
    else ""
)


@lru_cache(maxsize=1)
def _jwk_client():
    import jwt  # PyJWT（延遲匯入：本機未裝也不影響 AUTH_ENABLED=false 路徑）

    return jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json")


def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency：回傳 user_id（Cognito sub 或本機 dev user）。"""
    if not AUTH_ENABLED:
        return DEV_USER

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    import jwt

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            issuer=ISSUER,
        )
    except Exception as e:  # noqa: BLE001 — 任何驗證失敗都當 401
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing sub")
    return sub


def auth_config() -> dict:
    """供前端自我設定的公開資訊（不含任何密鑰）。"""
    return {
        "auth_enabled": AUTH_ENABLED,
        "region": COGNITO_REGION,
        "user_pool_id": COGNITO_USER_POOL_ID,
        "client_id": COGNITO_CLIENT_ID,
    }
