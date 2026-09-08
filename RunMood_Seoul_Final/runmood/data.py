import json
import pandas as pd
from .config import COURSE_CSV, ROUTES_GEOJSON

NUMERIC_COLUMNS = [
    "latitude","longitude","distance_km","toilet_count_300m","water_count_300m",
    "water_count_700m","elevation_gain_m","elevation_loss_m","avg_grade_pct",
    "max_grade_pct","walk_network_match_ratio","surface_known_ratio",
    "surface_paved_ratio","surface_unpaved_ratio","lit_known_ratio","lit_yes_ratio",
    "lit_no_ratio","foot_known_ratio","foot_allowed_ratio","pedestrian_path_ratio",
    "cycleway_ratio","steps_sample_ratio","smoothness_known_ratio","smoothness_good_ratio",
    "live_air_pm10","live_air_pm25","live_air_cai"
]

def load_courses():
    df = pd.read_csv(COURSE_CSV, encoding="utf-8-sig").fillna("")
    for c in NUMERIC_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["course_id"] = df["course_id"].astype(str)
    return df

def load_routes():
    with open(ROUTES_GEOJSON, encoding="utf-8") as f:
        return json.load(f)

def get_route_feature(course_id):
    for feature in load_routes().get("features", []):
        if str(feature.get("properties", {}).get("course_id")) == str(course_id):
            return feature
    return None

def _get(row, name, default=""):
    if isinstance(row, dict):
        v = row.get(name, default)
    else:
        v = getattr(row, name, default)
    return default if v is None else v

def row_to_document(row) -> str:
    g=lambda n,d="": _get(row,n,d)
    return (
        f"코스명 {g('course_name')}. 지역 {g('district')}. 거리 {g('distance_km')}km. "
        f"난이도 {g('difficulty_final', g('difficulty_derived'))}. 지형 {g('terrain')}. 환경 {g('environment')}. "
        f"분위기 {g('mood_tags_derived', g('mood_tags'))}. 고도 {g('elevation_summary')}. "
        f"주변 공원 {g('nearby_parks')}. 화장실 {g('toilet_count_300m')}개, 음수대 {g('water_count_300m')}개. "
        f"보행 네트워크 일치율 {g('walk_network_match_ratio')}%, 보행등급 {g('walkability_grade')}. "
        f"노면 {g('surface_top')}, 포장비율 {g('surface_paved_ratio')}%, 비포장비율 {g('surface_unpaved_ratio')}%. "
        f"야간조명 정보커버리지 {g('lit_known_ratio')}%, 확인구간 조명비율 {g('lit_yes_ratio')}%. "
        f"보행허용비율 {g('foot_allowed_ratio')}%, 보행로비율 {g('pedestrian_path_ratio')}%, 계단비율 {g('steps_sample_ratio')}%. "
        f"현재 대기질 {g('live_air_summary')}. 2025 평균 PM10 {g('air2025_pm10_avg')}, PM2.5 {g('air2025_pm25_avg')}. "
        f"실시간 도시데이터 영역 {g('realtime_area_names', g('nearest_realtime_area_name'))}. 설명 {g('description')}."
    )
