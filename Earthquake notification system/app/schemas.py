from pydantic import BaseModel, Field


class ConfigRequest(BaseModel):
    device_id: str
    magnitude_min: float = Field(ge=0, le=10)
    distance_km: float = Field(gt=0, le=5000)


class LocationRequest(BaseModel):
    device_id: str
    # 前端已用 h3-js latLngToCell 算好的 H3 index（server 不換算）。
    cell: str


class EarthquakeRequest(BaseModel):
    """模擬事件來源送入的地震事件。"""
    lat: float = Field(ge=-90, le=90)
    long: float = Field(ge=-180, le=180)
    magnitude: float = Field(ge=0, le=10)
    alert_id: str | None = None   # 同一起地震事件的識別；不給則自動產生
    version: int | None = None    # 事件更新版本；較新版本覆蓋較舊（supersession）
