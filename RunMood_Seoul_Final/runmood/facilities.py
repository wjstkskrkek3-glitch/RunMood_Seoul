from functools import lru_cache
from pathlib import Path
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import transform as geom_transform

BASE_DIR = Path(__file__).resolve().parent.parent
WATER_CSV = BASE_DIR / "data" / "water_points.csv"
_TO_METERS = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True).transform


def _flatten_route_geometry(feature):
    if not feature or not feature.get("geometry"):
        return []
    geom = feature["geometry"]
    coords = geom.get("coordinates", [])
    if geom.get("type") == "LineString":
        return [[float(p[0]), float(p[1])] for p in coords if len(p) >= 2]
    if geom.get("type") == "MultiLineString":
        out = []
        for line in coords:
            out.extend([[float(p[0]), float(p[1])] for p in line if len(p) >= 2])
        return out
    return []


def _route_shape(feature):
    if not feature or not feature.get("geometry"):
        return None
    geom = feature["geometry"]
    if geom.get("type") == "LineString":
        lines = [geom.get("coordinates", [])]
    elif geom.get("type") == "MultiLineString":
        lines = geom.get("coordinates", [])
    else:
        return None
    valid = []
    for line in lines:
        pts = [(float(p[0]), float(p[1])) for p in line if len(p) >= 2]
        if len(pts) >= 2:
            valid.append(LineString(pts))
    if not valid:
        return None
    return valid[0] if len(valid) == 1 else MultiLineString(valid)


@lru_cache(maxsize=1)
def load_water_points():
    if not WATER_CSV.exists():
        return pd.DataFrame(columns=["name", "longitude", "latitude", "address"])
    df = pd.read_csv(WATER_CSV, encoding="utf-8-sig").fillna("")
    for c in ("longitude", "latitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["longitude", "latitude"]).copy()


def water_near_route(feature, radius_m=300, limit=None):
    """Return actual Seoul park drinking-water points near the route line."""
    route = _route_shape(feature)
    if route is None:
        return []
    route_m = geom_transform(_TO_METERS, route)
    rows = []
    for r in load_water_points().itertuples(index=False):
        p = Point(float(r.longitude), float(r.latitude))
        distance = geom_transform(_TO_METERS, p).distance(route_m)
        if distance <= radius_m:
            rows.append({
                "category": "음수대",
                "name": str(r.name) if str(r.name).strip() else "공원 음수대",
                "longitude": float(r.longitude),
                "latitude": float(r.latitude),
                "distance_m": int(round(distance)),
                "address": str(r.address),
                "source": "서울시 공원음수대",
            })
    rows.sort(key=lambda x: x["distance_m"])
    return rows if limit is None else rows[:limit]


def route_anchor_points(feature, max_points=5):
    """Choose evenly distributed points along the route for external POI search."""
    coords = _flatten_route_geometry(feature)
    if not coords:
        return []
    max_points = max(1, int(max_points))
    if len(coords) <= max_points:
        return coords
    # 시작/끝에만 치우치지 않도록 전체 구간을 균등하게 나눈 중심 지점을 사용한다.
    fractions = [(i + 0.5) / max_points for i in range(max_points)]
    out = []
    for f in fractions:
        idx = min(len(coords) - 1, max(0, round((len(coords) - 1) * f)))
        out.append(coords[idx])
    return out
