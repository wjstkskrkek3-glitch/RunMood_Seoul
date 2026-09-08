import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote

import requests

from .config import KMA_API_KEY


KST = ZoneInfo("Asia/Seoul")
KMA_ULTRA_NOW_URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getUltraSrtNcst"
)


def latlon_to_grid(latitude: float, longitude: float) -> tuple[int, int]:
    """위도/경도(WGS84)를 기상청 단기예보 격자(nx, ny)로 변환한다."""
    re = 6371.00877
    grid = 5.0
    slat1 = 30.0
    slat2 = 60.0
    olon = 126.0
    olat = 38.0
    xo = 43.0
    yo = 136.0

    degrad = math.pi / 180.0

    re /= grid
    slat1 *= degrad
    slat2 *= degrad
    olon *= degrad
    olat *= degrad

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(
        math.pi * 0.25 + slat1 * 0.5
    )
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)

    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn

    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)

    ra = math.tan(
        math.pi * 0.25 + (float(latitude) * degrad) * 0.5
    )
    ra = re * sf / math.pow(ra, sn)

    theta = float(longitude) * degrad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = int(math.floor(ra * math.sin(theta) + xo + 0.5))
    y = int(math.floor(ro - ra * math.cos(theta) + yo + 0.5))
    return x, y


def _base_datetime(now: datetime | None = None) -> datetime:
    """
    초단기실황은 매시 정각 관측값이 제공된다.
    공개 지연을 고려해 매시 40분 이전에는 직전 시각을 사용한다.
    """
    now = now.astimezone(KST) if now else datetime.now(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < 40:
        base -= timedelta(hours=1)
    return base


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pty_text(value) -> str:
    code = str(value).strip()
    return {
        "0": "없음",
        "1": "비",
        "2": "비/눈",
        "3": "눈",
        "5": "빗방울",
        "6": "빗방울/눈날림",
        "7": "눈날림",
    }.get(code, f"알 수 없음({code})")


def get_current_weather(latitude: float, longitude: float) -> dict:
    """
    현재 위치의 기상청 초단기실황을 조회한다.

    반환 예:
    {
        "ok": True,
        "temperature_c": 24.1,
        "humidity_pct": 60.0,
        "wind_speed_mps": 2.3,
        "rain_1h_mm": 0.0,
        "precipitation_type": "없음",
        "nx": 60,
        "ny": 127,
        ...
    }
    """
    if not KMA_API_KEY:
        return {
            "ok": False,
            "error": "KMA_API_KEY가 설정되어 있지 않습니다.",
        }

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return {"ok": False, "error": "위도/경도 값이 올바르지 않습니다."}

    nx, ny = latlon_to_grid(latitude, longitude)
    base = _base_datetime()

    # 공공데이터포털에서 Encoding 키를 복사한 경우도 동작하도록 1회 decode
    service_key = unquote(KMA_API_KEY.strip())

    params = {
        "serviceKey": service_key,
        "pageNo": 1,
        "numOfRows": 1000,
        "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"),
        "base_time": base.strftime("%H00"),
        "nx": nx,
        "ny": ny,
    }

    try:
        response = requests.get(KMA_ULTRA_NOW_URL, params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"기상청 API 연결 실패: {e}",
            "nx": nx,
            "ny": ny,
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "error": "기상청 응답을 JSON으로 해석할 수 없습니다.",
            "raw": response.text[:300],
            "nx": nx,
            "ny": ny,
        }

    header = (
        data.get("response", {})
        .get("header", {})
    )
    result_code = str(header.get("resultCode", ""))
    result_msg = str(header.get("resultMsg", ""))

    if result_code != "00":
        return {
            "ok": False,
            "error": f"기상청 API 오류 {result_code}: {result_msg}",
            "nx": nx,
            "ny": ny,
        }

    items = (
        data.get("response", {})
        .get("body", {})
        .get("items", {})
        .get("item", [])
    )

    if not items:
        return {
            "ok": False,
            "error": "기상청에서 현재 위치의 실황 데이터가 반환되지 않았습니다.",
            "nx": nx,
            "ny": ny,
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H00"),
        }

    values = {}
    obs_date = None
    obs_time = None

    for item in items:
        category = item.get("category")
        if category:
            values[category] = item.get("obsrValue")
        obs_date = obs_date or item.get("baseDate")
        obs_time = obs_time or item.get("baseTime")

    rain = _to_float(values.get("RN1"))
    temperature = _to_float(values.get("T1H"))
    humidity = _to_float(values.get("REH"))
    wind_speed = _to_float(values.get("WSD"))
    wind_direction = _to_float(values.get("VEC"))

    pty_raw = values.get("PTY", "0")
    pty = _pty_text(pty_raw)

    return {
        "ok": True,
        "source": "기상청 단기예보 조회서비스 - 초단기실황",
        "latitude": latitude,
        "longitude": longitude,
        "nx": nx,
        "ny": ny,
        "base_date": obs_date or base.strftime("%Y%m%d"),
        "base_time": obs_time or base.strftime("%H00"),
        "temperature_c": temperature,
        "humidity_pct": humidity,
        "wind_speed_mps": wind_speed,
        "wind_direction_deg": wind_direction,
        "rain_1h_mm": rain,
        "precipitation_type": pty,
        "raw_categories": values,
    }


def running_weather_message(weather: dict) -> str:
    """러닝 화면에 표시할 간단한 날씨 안내 문구를 만든다."""
    if not weather or not weather.get("ok"):
        return "날씨 정보를 확인할 수 없습니다."

    temp = weather.get("temperature_c")
    humidity = weather.get("humidity_pct")
    wind = weather.get("wind_speed_mps")
    rain = weather.get("rain_1h_mm")
    pty = weather.get("precipitation_type", "없음")

    warnings = []

    if rain is not None and rain > 0:
        warnings.append("비가 오고 있어 미끄러운 노면을 주의하세요.")
    elif pty != "없음":
        warnings.append(f"현재 강수 형태는 {pty}입니다.")

    if temp is not None:
        if temp >= 30:
            warnings.append("기온이 높아 수분 보충과 무리하지 않는 러닝이 필요합니다.")
        elif temp <= 0:
            warnings.append("기온이 낮아 충분한 워밍업과 방한이 필요합니다.")

    if humidity is not None and humidity >= 80:
        warnings.append("습도가 높아 체감 부담이 커질 수 있습니다.")

    if wind is not None and wind >= 8:
        warnings.append("바람이 강해 개방된 구간에서는 주의가 필요합니다.")

    return " ".join(warnings) if warnings else "현재 날씨는 러닝하기에 큰 기상 경고가 없습니다."
