import requests
from .config import KAKAO_REST_API_KEY

BASE_URL = "https://dapi.kakao.com/v2/local/search"
CATEGORY = {
    "편의점": "CS2",
    "지하철역": "SW8",
    "카페": "CE7",
    "약국": "PM9",
    "병원": "HP8",
}


def _headers():
    return {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}


def _status_from_response(r):
    if r.status_code == 200:
        return "ok"
    if r.status_code in (401, 403):
        return "unauthorized"
    if r.status_code == 429:
        return "rate_limited"
    return f"http_{r.status_code}"


def check_connection(timeout=5):
    if not KAKAO_REST_API_KEY:
        return "missing"
    try:
        r = requests.get(
            f"{BASE_URL}/category.json",
            headers=_headers(),
            params={"category_group_code": "CS2", "x": 126.9780, "y": 37.5665, "radius": 100},
            timeout=timeout,
        )
        return _status_from_response(r)
    except requests.RequestException:
        return "network_error"


def _normalize(doc, category):
    try:
        lon = float(doc.get("x"))
        lat = float(doc.get("y"))
    except (TypeError, ValueError):
        return None
    distance = doc.get("distance")
    return {
        "category": category,
        "name": doc.get("place_name", ""),
        "distance_m": int(distance) if str(distance).isdigit() else None,
        "address": doc.get("road_address_name") or doc.get("address_name", ""),
        "longitude": lon,
        "latitude": lat,
        "phone": doc.get("phone", ""),
        "url": doc.get("place_url", ""),
        "source": "Kakao Local",
    }


def _request(endpoint, params, category, timeout=6):
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=_headers(), params=params, timeout=timeout)
    except requests.RequestException as e:
        return [], "network_error", str(e)
    status = _status_from_response(r)
    if status != "ok":
        detail = ""
        try:
            detail = r.json().get("msg") or r.json().get("message") or ""
        except Exception:
            detail = r.text[:120]
        return [], status, detail
    docs = []
    for d in r.json().get("documents", []):
        item = _normalize(d, category)
        if item:
            docs.append(item)
    return docs, "ok", ""


def nearby_route(anchor_points, radius=700, per_category=5):
    """Search Kakao POIs around distributed route anchors and deduplicate them.

    Returns (facilities, status, message). On 401/403 it stops immediately so the
    terminal is not flooded with repeated Unauthorized errors.
    """
    if not KAKAO_REST_API_KEY:
        return [], "missing", "KAKAO_REST_API_KEY가 비어 있습니다."
    anchors = list(anchor_points or [])
    if not anchors:
        return [], "no_anchor", "코스 좌표를 찾지 못했습니다."

    out = []
    seen = set()
    for lon, lat in anchors:
        common = {"x": lon, "y": lat, "radius": radius, "size": per_category, "sort": "distance"}
        # Public toilets have no Kakao category group code, so use keyword search.
        toilet_docs, status, msg = _request(
            "keyword.json", {**common, "query": "공중화장실"}, "화장실"
        )
        if status != "ok":
            return out, status, msg
        for item in toilet_docs:
            key = (item["category"], item["name"], round(item["longitude"], 6), round(item["latitude"], 6))
            if key not in seen:
                seen.add(key); out.append(item)

        for label, code in CATEGORY.items():
            docs, status, msg = _request("category.json", {**common, "category_group_code": code}, label)
            if status != "ok":
                return out, status, msg
            for item in docs:
                key = (item["category"], item["name"], round(item["longitude"], 6), round(item["latitude"], 6))
                if key not in seen:
                    seen.add(key); out.append(item)
    return out, "ok", ""


# Backward-compatible helper for any code still calling nearby(lat, lon).
def nearby(lat, lon, radius=700):
    facilities, _, _ = nearby_route([[float(lon), float(lat)]], radius=radius)
    return facilities
