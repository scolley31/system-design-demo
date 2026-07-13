"""server 端 H3 geo helpers（技術選型：H3，見 DESIGN 附錄 A）。

分工：
- **裝置端**用 h3-js `latLngToCell` 算好自己的 cell 直送（server 不換算使用者位置）。
- **server 端**只需：
  1. 把「震央 + 震度」換成覆蓋圓形影響範圍的 cells（gridDisk，圓形擴散剛好貼合 H3 六邊形等距鄰居）。
  2. `cell_to_latlng` 取 cell 中心，對候選裝置做距離二次裁切（因為只存 cell）。

震央本身是事件來源送來的 lat/long，server 把它換成中心 cell 是合理的（規則「不換算」是針對使用者位置）。
"""
import math
import os

import h3

# 前後端約定一致的 H3 解析度。改這裡的話，前端 static/index.html 的 H3_RES 也要一起改。
RES = int(os.getenv("H3_RES", "5"))

# 各解析度的平均六邊形邊長（km）——把「影響半徑」換成 gridDisk 要幾圈 k。
_EDGE_KM_BY_RES = {3: 59.8, 4: 22.6, 5: 8.54, 6: 3.23, 7: 1.22, 8: 0.46}


def magnitude_to_radius_km(magnitude: float) -> float:
    """粗略「震度 → 有感半徑」（demo 用），連續公式：半徑隨震度平滑成長。

    radius = 20 * sqrt(5)^(magnitude - 4)，約每 +1 級半徑 ×2.24：
      M4≈20 · M5≈45 · M6≈100 · M7≈224 · M8≈500 km（夾在 5~800）。
    真實系統改吃地震來源提供的 shaking polygon，直接用 h3.polygon_to_cells 覆蓋。
    """
    radius = 20.0 * (5.0 ** (0.5 * (magnitude - 4.0)))
    return round(max(5.0, min(radius, 800.0)), 1)


def cells_for_event(lat: float, lng: float, magnitude: float) -> tuple[list[str], float]:
    """震央 + 震度 → 覆蓋圓形影響範圍的 H3 cells。回 (cells, radius_km)。"""
    radius = magnitude_to_radius_km(magnitude)
    center = h3.latlng_to_cell(lat, lng, RES)
    edge = _EDGE_KM_BY_RES.get(RES, 8.54)
    k = max(1, math.ceil(radius / edge))
    cells = list(h3.grid_disk(center, k))
    return cells, radius


def cell_center(cell: str) -> tuple[float, float]:
    """回 cell 中心 (lat, lng)，供距離二次裁切用。"""
    return h3.cell_to_latlng(cell)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
