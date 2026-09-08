import re
import math
import random
from .intent import parse_intent
from .vector_store import semantic
from .kakao import nearby_route
from .data import load_courses, get_route_feature
from .facilities import water_near_route, route_anchor_points

RUN_LEVELS = {
    "초급": (1.4, 5.0),
    "중급": (5.0, 10.0),
    "고급": (10.0, 15.0),
    "전문가": (15.0, None),
}

LEVEL_ALIASES = {
    "쉬움": "초급",
    "보통": "중급",
    "어려움": "고급",
    "초보": "초급",
    "상급": "고급",
}


def num(v,default=0.0):
    try: return float(v)
    except Exception: return default


def contains(text,vals):
    t=str(text).lower()
    return any(str(v).lower() in t for v in vals if v)


def normalize_level(value):
    if not value:
        return None
    v = str(value).strip()
    if v in RUN_LEVELS:
        return v
    return LEVEL_ALIASES.get(v)


def course_running_level(km):
    km = num(km, -1)
    if km < 0:
        return None
    if km < 1.4:
        return "1.4km 미만"
    if km < 5:
        return "초급"
    if km < 10:
        return "중급"
    if km < 15:
        return "고급"
    return "전문가"


def level_range_text(level):
    return {
        "초급": "1.4~5km",
        "중급": "5~10km",
        "고급": "10~15km",
        "전문가": "15km 이상",
        "1.4km 미만": "1.4km 미만",
    }.get(level, "")


def numeric_distance_windows(target):
    """숫자 거리 요청은 정확히 맞추지 않지만, 과도하게 먼 코스도 허용하지 않는다."""
    if not target:
        return None, None
    target = float(target)

    # 5km -> 1차 4~6km, 2차 3.5~6.5km
    primary_tol = max(1.0, min(2.0, target * 0.20))
    extended_tol = max(1.5, min(3.0, target * 0.30))
    return (
        (max(0.0, target-primary_tol), target+primary_tol),
        (max(0.0, target-extended_tol), target+extended_tol),
    )


def distance_score(km,target):
    if not target:
        return 1.0
    target = float(target)
    gap = abs(num(km) - target)
    _, extended = numeric_distance_windows(target)
    tolerance = max(extended[1] - target, 0.1)
    return max(0.0, 1.0 - gap / tolerance)


def _terrain_ok_for_level(row, level):
    """거리 등급은 유지하되 '가볍게/적당히' 의미를 깨는 극단적 산악 코스는 막는다."""
    if not level:
        return True

    final_diff = str(row.get("difficulty_final", "")).strip()
    max_grade = num(row.get("max_grade_pct"))
    gain = num(row.get("elevation_gain_m"))
    km = max(num(row.get("distance_km")), 0.1)
    gain_per_km = gain / km

    if level == "초급":
        # 1.4~5km라도 인왕/남산 급경사 같은 코스는 '가볍게'로 추천하지 않음.
        if final_diff == "어려움":
            return False
        if max_grade >= 20 or gain_per_km >= 55:
            return False

    if level == "중급":
        # 중급은 적당한 러닝을 목표로 하므로 매우 강한 산악 코스만 제외.
        if final_diff == "어려움" and (max_grade >= 25 or gain_per_km >= 45):
            return False

    return True



def _haversine_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1)
    dl = math.radians(lon2-lon1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1.0, math.sqrt(h)))


def _interp(a, b, f):
    return [
        a[0] + (b[0]-a[0]) * f,
        a[1] + (b[1]-a[1]) * f,
    ]


def _trim_feature_to_km(feature, target_km):
    """원본 GeoJSON은 건드리지 않고 주변시설 검색용 표시 구간 feature만 만든다."""
    if not feature or not target_km or target_km <= 0:
        return feature

    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "LineString":
        source_lines = [coords]
    elif gtype == "MultiLineString":
        source_lines = coords
    else:
        return feature

    remaining = float(target_km) * 1000.0
    result = []

    for raw_line in source_lines:
        if remaining <= 0:
            break
        line = [[float(p[0]), float(p[1])] for p in raw_line if len(p) >= 2]
        if len(line) < 2:
            continue

        new_line = [line[0]]
        for i in range(1, len(line)):
            a, b = line[i-1], line[i]
            seg = _haversine_m(a, b)
            if seg <= 0:
                continue
            if seg <= remaining:
                new_line.append(b)
                remaining -= seg
            else:
                new_line.append(_interp(a, b, remaining/seg))
                remaining = 0
                break

        if len(new_line) >= 2:
            result.append(new_line)

    if not result:
        return feature

    new_feature = dict(feature)
    new_feature["geometry"] = {
        "type": "LineString" if len(result) == 1 else "MultiLineString",
        "coordinates": result[0] if len(result) == 1 else result,
    }
    return new_feature


def difficulty_score(course_km, wanted):
    wanted = normalize_level(wanted)
    if not wanted:
        return 1.0
    return 1.0 if course_running_level(course_km) == wanted else 0.0



RETURN_QUERY_WORDS = [
    "순환", "순환형", "한바퀴", "한 바퀴", "돌아오는", "돌아오게",
    "출발지 복귀", "원점 복귀", "시작점 복귀", "제자리로", "시작점으로"
]

def wants_return_route(query):
    q=str(query or "").replace(" ","")
    return any(w.replace(" ","") in q for w in RETURN_QUERY_WORDS)

def _place_terms(query):
    terms = set(re.findall(r"[가-힣]{2,10}(?:천|강)", query))
    if "한강" in query:
        terms.add("한강")
    return sorted(terms)


def _is_gps_art(row):
    """GPS 아트 여부는 경로 구조(loop)와 분리해서 관리한다."""
    v = row.get("is_gps_art", False)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes", "y"}

def _row_text(row):
    return " ".join(str(row.get(k,"")) for k in [
        "course_name","district","terrain","environment","mood_tags_derived",
        "nearby_parks","description","surface_top","highway_top"
    ])


def _place_identity_text(row):
    # 장소 이름을 판정할 때 description까지 사용하면
    # '불광천이 한강에 닿는 길' 같은 설명 때문에 한강 코스로 오인될 수 있다.
    return " ".join(str(row.get(k,"")) for k in [
        "course_name","environment","nearby_parks"
    ])


def _place_match(row, explicit_places):
    if not explicit_places:
        return True
    text=_place_identity_text(row)
    return any(term in text for term in explicit_places)


# v21.6 감성추천:
# 파서가 만든 감성 태그를 코스의 설명/환경/파생 감성 태그와 연결한다.
# 감성 요청이 있을 때는 감성에 맞는 후보군 안에서 기존 랜덤 추천을 수행한다.
MOOD_EMOJIS = {
    '힐링': '🌿',
    '스트레스해소': '😮\u200d💨',
    '분노해소': '😤',
    '우울위로': '🫂',
    '답답함해소': '🌬️',
    '외로움위로': '🤍',
    '조용함': '🤫',
    '야경': '🌃',
    '자연': '🌳',
    '개방감': '🌊',
    '활력': '⚡',
    '성취도전': '🏆',
}

MOOD_MATCH_WORDS = {
    "힐링": ["힐링", "편안", "여유", "휴식", "기분전환", "산책", "잔잔", "평온", "자연", "숲", "녹지"],
    "스트레스해소": ["힐링", "기분전환", "여유", "자연", "숲", "녹지", "하천", "강변", "개방감", "시원"],
    "분노해소": ["활력", "에너지", "기분전환", "개방감", "시원", "강변", "하천", "공원", "운동", "도전"],
    "우울위로": ["힐링", "편안", "여유", "평온", "잔잔", "자연", "숲", "녹지", "공원", "물가"],
    "답답함해소": ["개방감", "탁 트", "시원", "전망", "조망", "한강", "강변", "하천", "광장", "물가"],
    "외로움위로": ["힐링", "여유", "평온", "잔잔", "자연", "공원", "숲", "물가", "야경", "산책"],
    "조용함": ["조용", "한적", "여유", "사람 적", "북적이지", "차분", "평온", "고요"],
    "야경": ["야경", "야간", "밤", "저녁", "노을", "석양", "빛", "조명", "한강"],
    "자연": ["자연", "숲", "나무", "녹지", "공원", "산림", "생태", "하천", "강"],
    "개방감": ["개방감", "탁 트", "시원", "전망", "조망", "한강", "강변", "하천", "광장"],
    "활력": ["활력", "에너지", "신나는", "활기", "도전", "운동", "스피드", "성취감"],
    "성취도전": ["도전", "성취", "성취감", "장거리", "오르막", "산림", "트레일", "운동", "스피드"],
}


def _mood_match_count(row, moods):
    """요청 감성 중 코스 텍스트에 실제로 대응되는 태그 수를 반환한다."""
    if not moods:
        return 0
    text = _row_text(row).lower()
    matched = 0
    for mood in moods:
        key = str(mood).strip()
        words = MOOD_MATCH_WORDS.get(key, [key])
        if any(str(w).lower() in text for w in words if w):
            matched += 1
    return matched


def _filter_mood_candidates(pool, moods):
    """감성 요청이 있으면 가장 많이 일치하는 후보군에서 랜덤 추천한다.

    복수 감성 요청에서는 한 가지 감성만 맞는 코스보다 여러 감성이 함께 맞는
    코스를 우선 후보군으로 만든다. 감성 데이터가 전혀 매칭되지 않을 때만
    기존 전체 후보로 폴백하여 빈 추천을 만들지 않는다.
    """
    if not moods or not pool:
        return pool

    scored = [(x, _mood_match_count(x[2], moods)) for x in pool]
    best_hits = max((hits for _, hits in scored), default=0)
    if best_hits <= 0:
        return pool
    return [x for x, hits in scored if hits == best_hits]


def _query_requests_strong_effort(query):
    """감성 문장 속 '힘들다'와 명시적인 고강도 러닝 요청을 구분한다."""
    q = str(query or '').lower().replace(' ', '')
    words = ['고강도','도전코스','도전적인코스','빡세게','빡센코스','힘든코스','전문가','고급','상급','기록갱신','기록 갱신','한계도전']
    return any(w.replace(' ', '') in q for w in words)


def _limit_emotional_default_distance(pool, query, intent, explicit_level):
    """감성만 말한 사용자가 15km+ 전문가 코스를 우연히 뽑는 것을 막는다."""
    if not pool or not intent.mood:
        return pool
    if intent.distance_km or explicit_level or _query_requests_strong_effort(query):
        return pool
    limited = [x for x in pool if num(x[2].get('distance_km')) < 15.0]
    return limited or pool


def _diversify_ranked(pool, top_k):
    """같은 지역/유형이 상위권을 독점하지 않도록 1차 추천을 다양화한다.

    점수 순서를 크게 깨지 않되, 같은 대표 자치구 + 같은 route_type은
    이미 뽑힌 조합일수록 뒤로 보낸다. 사용자 제공 GPX도 별도 출처로
    인식되어 자연스럽게 섞일 수 있게 한다.
    """
    remaining=list(pool)
    chosen=[]
    district_used={}
    type_used={}
    source_used={}

    def primary_district(row):
        d=str(row.get("district", "서울") or "서울")
        return d.split("|")[0]

    while remaining and len(chosen) < top_k:
        best_idx=0
        best_value=None
        for idx, item in enumerate(remaining):
            score, cid, row=item
            district=primary_district(row)
            rtype=str(row.get("route_type", "point_to_point") or "point_to_point")
            src=str(row.get("source", "") or "")
            src_group="gpx_v21" if "RunSpot GPX" in src else "base"
            # 동일 지역 반복을 가장 강하게 억제하고, 동일 유형/출처는 약하게 억제.
            penalty=(district_used.get(district,0)*0.055
                     + type_used.get(rtype,0)*0.012
                     + source_used.get(src_group,0)*0.008)
            value=float(score)-penalty
            if best_value is None or value > best_value:
                best_value=value
                best_idx=idx
        item=remaining.pop(best_idx)
        chosen.append(item)
        row=item[2]
        district=primary_district(row)
        rtype=str(row.get("route_type", "point_to_point") or "point_to_point")
        src=str(row.get("source", "") or "")
        src_group="gpx_v21" if "RunSpot GPX" in src else "base"
        district_used[district]=district_used.get(district,0)+1
        type_used[rtype]=type_used.get(rtype,0)+1
        source_used[src_group]=source_used.get(src_group,0)+1
    return chosen


def _query_requests_fresh_results(query):
    q=str(query or "").replace(" ", "")
    words=["다른","새로운","새코스","다음","또추천","겹치지","안나온","안보여준"]
    return any(w.replace(" ", "") in q for w in words)


def _select_ranked(ranked, top_k, target_km, explicit_places, requested_level=None):
    """v17: 등급은 거리구간 HARD FILTER, 숫자 km는 제한된 유연 범위."""
    pool = ranked

    # 명시 장소가 실제 후보에 존재하면 장소 조건을 우선한다.
    if explicit_places:
        place_pool = [x for x in pool if _place_match(x[2], explicit_places)]
        if place_pool:
            pool = place_pool

    requested_level = normalize_level(requested_level)

    # 1) 사용자가 초급/중급/고급/전문가를 말했다면 해당 거리 구간 밖은 추천 금지.
    if requested_level:
        pool = [
            x for x in pool
            if course_running_level(x[2].get("distance_km")) == requested_level
            and _terrain_ok_for_level(x[2], requested_level)
        ]
        if not pool:
            return []

        # 등급 안에서 숫자 km까지 말했으면 그 숫자에 가까운 순으로 정렬.
        if target_km:
            target = float(target_km)
            pool.sort(key=lambda x: (
                abs(num(x[2].get("distance_km")) - target),
                -x[0]
            ))
            # 같은 거리권에서는 종합점수를 살린다.
            if len(pool) > 1:
                best_gap = abs(num(pool[0][2].get("distance_km")) - target)
                close = [x for x in pool if abs(num(x[2].get("distance_km"))-target) <= best_gap + 0.8]
                close.sort(key=lambda x: -x[0])
                rest = [x for x in pool if x not in close]
                pool = close + rest
        else:
            pool.sort(key=lambda x: -x[0])

        return pool[:top_k]

    # 2) 등급 없이 숫자만 말한 경우:
    #    1차 범위 -> 부족하면 2차 범위까지만 확대. 그 밖은 추천하지 않는다.
    if target_km:
        target = float(target_km)
        primary, extended = numeric_distance_windows(target)

        primary_pool = [
            x for x in pool
            if primary[0] <= num(x[2].get("distance_km")) <= primary[1]
        ]
        extended_pool = [
            x for x in pool
            if extended[0] <= num(x[2].get("distance_km")) <= extended[1]
        ]

        def order(items):
            return sorted(items, key=lambda x: (
                abs(num(x[2].get("distance_km")) - target),
                -x[0]
            ))

        selected = order(primary_pool)[:top_k]
        selected_ids = {x[1] for x in selected}

        if len(selected) < top_k:
            for x in order(extended_pool):
                if x[1] not in selected_ids:
                    selected.append(x)
                    selected_ids.add(x[1])
                    if len(selected) >= top_k:
                        break

        # 중요: extended 범위 밖의 10km 코스를 5km 요청에 억지로 채우지 않는다.
        return selected[:top_k]

    # 3) 거리/등급이 없으면 종합점수 순
    return pool[:top_k]

def recommend(query,top_k=3, excluded_course_ids=None, user_location=None):
    intent,parser=parse_intent(query)
    excluded_ids={str(x) for x in (excluded_course_ids or []) if x}

    # v21.1: 사용자가 문장에 직접 적은 러닝 등급을 OpenAI/규칙 파서보다 우선한다.
    explicit_level = None
    for token, level in [("초급자", "초급"), ("초급", "초급"), ("초보", "초급"),
                         ("중급", "중급"), ("고급", "고급"), ("전문가", "전문가"), ("상급", "고급")]:
        if token in query:
            explicit_level = level
            break
    if explicit_level:
        intent.difficulty = explicit_level

    # '초급 추천 5개'처럼 문장에 개수를 적으면 슬라이더보다 문장 요청을 우선한다.
    count_match = re.search(r"([1-5])\s*개", query)
    effective_top_k = int(count_match.group(1)) if count_match else top_k

    # '5번째 초급 추천'은 매번 1등을 다시 보여주는 대신 실제 5위 코스를 반환한다.
    ordinal_match = re.search(r"([1-9]\d*)\s*번째", query)
    ordinal = int(ordinal_match.group(1)) if ordinal_match else None

    # v21.6: 일반 추천에서도 GPS 아트형이 벡터 상위 일부에 들지 않았다는 이유로
    # 후보에서 빠지지 않도록 안전검증 DB 전체를 semantic 후보로 가져온다.
    # 최종 선택은 기존처럼 조건/감성 필터 후 random.sample을 사용하므로 랜덤 추천은 유지된다.
    full_df = load_courses()
    all_count = len(full_df)
    semantic_k = all_count
    raw=semantic(query,semantic_k)
    ids=list(raw["ids"][0]); metas=list(raw["metadatas"][0]); distances=list(raw.get("distances",[[]])[0])
    full=full_df.set_index("course_id",drop=False)

    # v22 patch: 기존 ChromaDB가 72개 인덱스인 상태에서도 새 GPX 18개를 후보에 포함한다.
    # DB를 재빌드하기 전에는 새 코스의 semantic 점수만 중립값을 사용하고,
    # 거리/지역/감정/환경/보행/노면/조명 점수는 정상 반영한다.
    seen_ids={str(x) for x in ids}
    for missing_cid in full.index.astype(str):
        if missing_cid not in seen_ids:
            ids.append(missing_cid)
            metas.append({})
            distances.append(1.0)
    explicit_places=_place_terms(query)
    return_requested=wants_return_route(query)

    # v21.6: GPS 아트형은 일반 추천에도 포함하되, 명시 요청일 때만 GPS 아트형으로 제한한다.
    # 사용자가 "아트형", "GPS 아트", "그림 코스" 등을 요청하면
    # route_type=gps_art 코스만 후보로 남긴 뒤 기존 랜덤 추천을 적용한다.
    q_lower = str(query).lower().replace("-", " ").replace("_", " ")
    art_requested = any(token in q_lower for token in (
        "아트형", "아트 코스", "아트코스", "gps 아트", "gps아트",
        "그림 코스", "그림코스", "모양 코스", "모양코스"
    ))
    ranked=[]

    for i,(cid,m) in enumerate(zip(ids,metas)):
        # v13: 균형 보행검증을 통과한 코스만 추천한다.
        # 예전 Vector DB에 격리 코스 ID가 남아 있어도 여기서 차단한다.
        if str(cid) not in full.index:
            continue
        row=full.loc[str(cid)].to_dict()
        if str(cid) in excluded_ids:
            continue
        recommendable = str(row.get("route_recommendable", "True")).strip().lower()
        if recommendable in {"false", "0", "no", "n"}:
            continue
        if str(row.get("route_safety_status", "")).strip().startswith("QUARANTINED"):
            continue

        # v18: '순환/돌아오는/출발지 복귀' 요청은 실제 출발지 복귀 코스만 사용.
        if return_requested and str(row.get("route_type","point_to_point")) != "loop":
            continue

        # v21.6: 아트형을 명시한 경우에만 GPS 아트 코스만 허용한다.
        if art_requested and not _is_gps_art(row):
            continue

        d=num(distances[i],1.0) if i<len(distances) else 1.0
        semantic_score=1/(1+max(d,0))
        score=semantic_score*0.24

        # 거리는 정확한 강제조건이 아니라 근접 선호도로 반영한다.
        score+=distance_score(row.get("distance_km",0),intent.distance_km)*0.18

        score+=difficulty_score(
            row.get("distance_km"),
            intent.difficulty
        )*0.12
        # v19_loop_bonus: 일반 검색에도 완전 순환형이 자연스럽게 섞이도록 소폭 우대
        if str(row.get("route_type","")) == "loop":
            score += 0.055

        text=_row_text(row)
        if intent.location!="서울" and intent.location in text: score+=0.10
        if intent.environment and contains(text,intent.environment): score+=0.05
        if intent.terrain and contains(text,intent.terrain): score+=0.04
        if intent.mood:
            mood_hits = _mood_match_count(row, intent.mood)
            if mood_hits:
                score += min(0.12, 0.06 * mood_hits)

        if explicit_places:
            identity=_place_identity_text(row)
            matched=sum(1 for term in explicit_places if term in identity)
            if matched:
                score += min(0.25, 0.18 * matched)
            else:
                score -= 0.18

        score+=min(num(row.get("walk_network_match_ratio"))/100,1)*0.05
        known=num(row.get("surface_known_ratio")); paved=num(row.get("surface_paved_ratio"))
        if known>=20: score+=min(paved/100,1)*0.025
        if intent.wants_night_view:
            lit_known=num(row.get("lit_known_ratio")); lit_yes=num(row.get("lit_yes_ratio"))
            if lit_known>=15: score+=min(lit_yes/100,1)*0.04
            if "야경" in text: score+=0.025
        pm25=num(row.get("live_air_pm25"),-1)
        if pm25>=0:
            if pm25<=15: score+=0.04
            elif pm25<=35: score+=0.02

        # GPS 선택 시 기존 추천점수는 유지하면서 가까운 코스에만 근접 가산점을 추가한다.
        if user_location:
            try:
                feat = get_route_feature(str(cid))
                geom = (feat or {}).get("geometry", {})
                coords = geom.get("coordinates", [])
                if geom.get("type") == "LineString" and coords:
                    start = [float(coords[0][0]), float(coords[0][1])]
                elif geom.get("type") == "MultiLineString" and coords and coords[0]:
                    start = [float(coords[0][0][0]), float(coords[0][0][1])]
                else:
                    start = [num(row.get("longitude")), num(row.get("latitude"))]
                user_pt = [float(user_location["longitude"]), float(user_location["latitude"])]
                if start[0] and start[1]:
                    user_distance_km = _haversine_m(user_pt, start) / 1000.0
                    row["user_distance_km"] = round(user_distance_km, 3)
                    # 10km 이내일수록 최대 +0.18, 그 밖은 0.
                    score += max(0.0, 1.0 - user_distance_km / 10.0) * 0.18
            except Exception:
                pass

        ranked.append((score,str(cid),row))

    ranked.sort(key=lambda x:x[0],reverse=True)
    requested_level=normalize_level(intent.difficulty)
    selection_k = max(effective_top_k, ordinal or 0)

    # v21.3 랜덤 추천:
    # 1) 사용자가 지정한 난이도/거리/장소/순환 조건으로 후보를 먼저 HARD FILTER한다.
    # 2) 추천 점수는 계산/표시만 유지하고, 일반 추천의 선택 순서에는 사용하지 않는다.
    # 3) 필터를 통과한 전체 후보에서 중복 없이 균등 무작위 추출한다.
    #    (이전 추천 코스는 app.py가 excluded_course_ids로 제외한다.)
    candidate_pool=_select_ranked(
        ranked, len(ranked), intent.distance_km, explicit_places, requested_level
    )

    # v21.7 상황형 감성추천: 감성 일치 후보군을 만든 뒤 기존 랜덤 추천을 유지한다.
    # 감성만 말하고 거리/난이도를 지정하지 않으면 15km 이상 전문가 코스는 기본 제외한다.
    candidate_pool=_filter_mood_candidates(candidate_pool, intent.mood)
    candidate_pool=_limit_emotional_default_distance(candidate_pool, query, intent, explicit_level)

    if ordinal:
        # '5번째'처럼 명시적 순위를 묻는 요청은 기존 의미를 보존해
        # 적합도 점수 기준 N번째를 보여준다. 일반 추천만 랜덤이다.
        selected = [candidate_pool[ordinal-1]] if len(candidate_pool) >= ordinal else []
    else:
        k=min(effective_top_k, len(candidate_pool))
        if user_location:
            # GPS 사용 시 가까운 코스 가산점이 실제 선택에 반영되도록 종합점수 순으로 선택한다.
            selected=sorted(candidate_pool, key=lambda x: x[0], reverse=True)[:k]
        else:
            # GPS를 사용하지 않을 때는 v21.3의 기존 랜덤 추천을 그대로 유지한다.
            selected=random.sample(candidate_pool, k) if k > 0 else []

    out=[]
    for score,cid,row in selected:
        item=dict(row)
        item["course_id"]=cid
        item["score"]=round(min(max(score,0.0),1.0),4)
        if user_location:
            item["user_location"] = dict(user_location)

        full_km=num(row.get("distance_km"))
        target=float(intent.distance_km) if intent.distance_km else None

        item["requested_distance_km"]=target
        item["full_distance_km"]=full_km
        item["running_level"]=course_running_level(full_km)
        item["running_level_range"]=level_range_text(item["running_level"])

        # v16: 5km라고 입력해도 코스를 억지로 5km로 자르거나 늘리지 않는다.
        # 검증된 완성 코스 전체를 그대로 보여준다.
        item["is_target_segment"]=False
        item["display_distance_km"]=full_km

        feat=get_route_feature(cid)
        facility_feat = feat

        # 시설 개수 제한 없음: 해당 표시 구간 300m 이내의 서울시 음수대를 모두 사용한다.
        item["water_facilities"] = water_near_route(
            facility_feat, radius_m=300, limit=None
        )

        # 표시 구간 전체를 5개 지점으로 균등 샘플링해 Kakao 주변시설을 조회한다.
        # 조회된 시설은 중복만 제거하고 지도 표시 개수는 제한하지 않는다.
        anchors=route_anchor_points(facility_feat, max_points=5)
        if not anchors and num(row.get("latitude")) and num(row.get("longitude")):
            anchors=[[num(row.get("longitude")),num(row.get("latitude"))]]
        facilities,status,message=nearby_route(anchors,radius=700,per_category=5)
        item["facilities"]=facilities
        item["kakao_status"]=status
        item["kakao_message"]=message

        reasons=[]
        if explicit_places:
            hit=[p for p in explicit_places if p in _place_identity_text(row)]
            if hit:
                reasons.append("지정 장소 일치: "+", ".join(hit))

        route_type = str(item.get("route_type", "")).strip().lower()
        if _is_gps_art(item):
            reasons.append("🔁 완전 순환형 · 🎨 GPS 아트")
        elif route_type in {"return","loop"}:
            reasons.append(
                "🔁 완전 순환형 · 다른 길로 출발지 복귀"
                if route_type=="loop"
                else "↩️ 출발지 복귀형"
            )
        else:
            reasons.append("➡️ 편도형")

        level = item.get("running_level")
        if level and level != "3km 미만":
            reasons.append(f"러닝 등급 {level}({level_range_text(level)})")

        if target:
            gap = abs(full_km-target)
            reasons.append(
                f"희망 거리 약 {target:g}km · 완성 코스 {full_km:.2f}km · "
                f"거리 차이 {gap:.2f}km"
            )

        if intent.mood:
            mood_reason = " · ".join(f"{MOOD_EMOJIS.get(m, '💚')} {m}" for m in intent.mood)
            reasons.append("감성 맞춤 " + mood_reason)
        if intent.environment: reasons.append("환경 "+", ".join(intent.environment))
        if intent.wants_night_view: reasons.append("야간조명/야경")
        if item.get("user_distance_km") is not None:
            reasons.append(f"현재 위치에서 시작점 약 {float(item['user_distance_km']):.2f}km")
        reasons.append("보행·노면·고도·대기질 종합")
        item["reason"]=" · ".join(reasons)
        out.append(item)

    return intent,out,parser
