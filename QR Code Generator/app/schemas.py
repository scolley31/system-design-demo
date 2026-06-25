from datetime import datetime

from pydantic import BaseModel


class CreateRequest(BaseModel):
    url: str
    expires_at: datetime | None = None


class CreateResponse(BaseModel):
    token: str
    short_url: str
    qr_code_url: str
    original_url: str


class QRInfoResponse(BaseModel):
    token: str
    original_url: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    is_deleted: bool

    model_config = {"from_attributes": True}


class UpdateRequest(BaseModel):
    # 第 2 題：PATCH 部分更新，兩欄皆可選。
    url: str | None = None
    expires_at: datetime | None = None
