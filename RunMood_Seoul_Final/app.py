import math
import json
import html
import re
import streamlit as st
import pydeck as pdk
import streamlit.components.v1 as components

try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

from runmood.recommender import recommend
from runmood.vector_store import build
from runmood.data import get_route_feature
from runmood.config import OPENAI_API_KEY, KAKAO_REST_API_KEY, SEOUL_API_KEY, KMA_API_KEY
from runmood.kakao import check_connection
from runmood.weather import get_current_weather, running_weather_message
from runmood.database import (
    init_schema, seed_default_user, authenticate_user, user_exists, nickname_exists,
    create_user, add_shared_course, list_shared_courses, increment_reaction
)

st.set_page_config(page_title="RunMood Seoul", page_icon="🏃", layout="wide")

# --- 전화번호 포맷팅 함수 (Python 안정적인 방식) ---
def format_phone_number(raw_num: str) -> str:
    digits = re.sub(r"[^0-9]", "", str(raw_num or ""))[:11]
    if len(digits) <= 3:
        return digits
    elif len(digits) <= 7:
        return f"{digits[:3]}-{digits[3:]}"
    elif len(digits) <= 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    else:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"

def on_phone_change():
    st.session_state.reg_phone = format_phone_number(st.session_state.reg_phone)

# --- PostgreSQL + pgVector DB 초기화 ---
@st.cache_resource(show_spinner=False)
def ensure_database():
    init_schema()
    seed_default_user()
    return True

try:
    ensure_database()
except Exception as e:
    st.error("PostgreSQL DB 연결에 실패했습니다. .env의 DATABASE_URL과 PostgreSQL/pgVector 실행 상태를 확인하세요.")
    st.code(str(e))
    st.stop()

# --- 세션 상태(Session State) 초기화: 화면/현재 사용자 상태만 유지 ---
if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None

if "page" not in st.session_state:
    st.session_state.page = "login"

if "reg_phone" not in st.session_state:
    st.session_state.reg_phone = ""

# 중복확인 관리 상태
if "id_checked" not in st.session_state:
    st.session_state.id_checked = False
if "checked_id_value" not in st.session_state:
    st.session_state.checked_id_value = ""
if "id_check_msg" not in st.session_state:
    st.session_state.id_check_msg = None

if "nick_checked" not in st.session_state:
    st.session_state.nick_checked = False
if "checked_nick_value" not in st.session_state:
    st.session_state.checked_nick_value = ""
if "nick_check_msg" not in st.session_state:
    st.session_state.nick_check_msg = None

if "pw_checked" not in st.session_state:
    st.session_state.pw_checked = False
if "pw_check_msg" not in st.session_state:
    st.session_state.pw_check_msg = None

if "recommend_results" not in st.session_state:
    st.session_state.recommend_results = None
if "selected_course" not in st.session_state:
    st.session_state.selected_course = None
if "user_conditions" not in st.session_state:
    st.session_state.user_conditions = {}
if "recommendation_history" not in st.session_state:
    st.session_state.recommendation_history = []
if "last_recommendation_signature" not in st.session_state:
    st.session_state.last_recommendation_signature = None
if "user_location" not in st.session_state:
    st.session_state.user_location = None


@st.cache_data(ttl=300, show_spinner=False)
def kakao_status_cached():
    return check_connection()


@st.cache_data(ttl=600, show_spinner=False)
def weather_cached(latitude, longitude):
    return get_current_weather(float(latitude), float(longitude))


def status_label(status):
    return {
        "ok": "실제 API 연결 ✅",
        "missing": "REST API 키 없음 🟡",
        "unauthorized": "인증 실패(401/403) ❌",
        "rate_limited": "요청 한도 초과 ⚠️",
        "network_error": "네트워크 오류 ⚠️",
    }.get(status, f"연결 오류 ({status}) ⚠️")


def haversine_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def interpolate_point(a, b, fraction):
    return [
        a[0] + (b[0] - a[0]) * fraction,
        a[1] + (b[1] - a[1]) * fraction,
    ]


def route_lines(feature):
    if not feature or not feature.get("geometry"):
        return []
    geom = feature["geometry"]
    coords = geom.get("coordinates", [])
    if geom.get("type") == "LineString":
        lines = [coords]
    elif geom.get("type") == "MultiLineString":
        lines = coords
    else:
        return []

    out = []
    for line in lines:
        pts = []
        for p in line:
            if len(p) >= 2:
                pts.append([float(p[0]), float(p[1])])
        if len(pts) >= 2:
            out.append(pts)
    return out


def flatten(lines):
    return [p for line in lines for p in line]


def trim_lines_to_km(lines, target_km):
    if not target_km or target_km <= 0:
        return lines

    remaining = float(target_km) * 1000.0
    result = []

    for line in lines:
        if remaining <= 0:
            break
        if len(line) < 2:
            continue

        new_line = [line[0]]
        for i in range(1, len(line)):
            a, b = line[i - 1], line[i]
            seg = haversine_m(a, b)

            if seg <= 0:
                continue

            if seg <= remaining:
                new_line.append(b)
                remaining -= seg
            else:
                f = remaining / seg
                new_line.append(interpolate_point(a, b, f))
                remaining = 0
                break

        if len(new_line) >= 2:
            result.append(new_line)

    return result if result else lines


def point_near_route_m(point, route_points):
    if not route_points:
        return 10**9
    return min(haversine_m(point, p) for p in route_points)


def facility_rows(item, display_points):
    rows = []
    rows.extend(item.get("water_facilities") or [])
    rows.extend(item.get("facilities") or [])

    icon_map = {
        "음수대": "🚰",
        "화장실": "🚻",
        "편의점": "🏪",
        "지하철역": "🚇",
        "카페": "☕",
        "약국": "💊",
        "병원": "🏥",
    }

    seen = set()
    clean = []
    for f in rows:
        try:
            lon = float(f.get("longitude"))
            lat = float(f.get("latitude"))
        except (TypeError, ValueError):
            continue

        key = (f.get("category", ""), f.get("name", ""), round(lon, 5), round(lat, 5))
        if key in seen:
            continue
        seen.add(key)

        x = dict(f)
        x["longitude"] = lon
        x["latitude"] = lat
        x["icon"] = icon_map.get(x.get("category", ""), "📍")
        x["route_display_distance_m"] = int(round(
            point_near_route_m([lon, lat], display_points)
        ))
        clean.append(x)

    clean.sort(key=lambda x: (
        x.get("category", "기타"),
        x.get("route_display_distance_m", 999999)
    ))
    return clean


def build_leaflet_map(item, feature, height=600):
    full_lines = route_lines(feature)
    if item.get("is_target_segment") and item.get("display_distance_km"):
        lines = trim_lines_to_km(full_lines, item.get("display_distance_km"))
    else:
        lines = full_lines

    points = flatten(lines)
    facilities = facility_rows(item, points)

    if not points:
        return None, facilities

    route_latlngs = [[[p[1], p[0]] for p in line] for line in lines if line]
    start_pt = [points[0][1], points[0][0]]
    end_pt = [points[-1][1], points[-1][0]]

    facility_payload = []
    for f in facilities:
        facility_payload.append({
            "lat": float(f["latitude"]),
            "lon": float(f["longitude"]),
            "icon": f.get("icon", "📍"),
            "category": str(f.get("category", "시설")),
            "name": str(f.get("name", "")),
            "address": str(f.get("address", "")),
            "distance": int(f.get("route_display_distance_m", 0) or 0),
        })

    route_json = json.dumps(route_latlngs, ensure_ascii=False)
    facility_json = json.dumps(facility_payload, ensure_ascii=False)
    start_json = json.dumps(start_pt)
    end_json = json.dumps(end_pt)
    user_location = item.get("user_location") or st.session_state.get("user_location")
    user_pt = None
    if user_location and user_location.get("latitude") is not None and user_location.get("longitude") is not None:
        user_pt = [float(user_location["latitude"]), float(user_location["longitude"])]
    user_json = json.dumps(user_pt)

    map_id = f"map_{str(item.get('course_id','course')).replace('-', '_')}_{int(item.get('display_distance_km',0)*100)}"

    html_code = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<style>
  html, body {{ margin:0; padding:0; background:#fff; }}
  #{map_id} {{ width:100%; height:{height}px; border-radius:10px; overflow:hidden; }}
  .facility-icon {{ width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:15px; line-height:22px; background:rgba(255,255,255,.96); border:1px solid rgba(40,40,40,.40); box-shadow:0 1px 3px rgba(0,0,0,.28); }}
  .facility-legend {{ position:absolute; right:10px; bottom:10px; z-index:999; max-width:440px; padding:7px 10px; background:rgba(255,255,255,.94); border:1px solid #bbb; border-radius:8px; font-family:Arial,"Malgun Gothic",sans-serif; font-size:12px; color:#222; box-shadow:0 1px 5px rgba(0,0,0,.20); }}
  .facility-legend span {{ margin-right:8px; white-space:nowrap; }}
  .marker-cluster-small div, .marker-cluster-medium div, .marker-cluster-large div {{ background:rgba(255,255,255,.90); color:white; font-weight:700; }}
  .marker-cluster-small, .marker-cluster-medium, .marker-cluster-large {{ background:rgba(255,255,255,.75); }}
  .start-icon, .end-icon, .user-icon {{ width:20px; height:20px; border-radius:50%; border:3px solid white; box-shadow:0 1px 5px rgba(0,0,0,.35); }}
  .start-icon {{ background:#14a36f; }}
  .end-icon {{ background:#f58220; }}
  .leaflet-popup-content {{ font-family:Arial, "Malgun Gothic", sans-serif; font-size:13px; line-height:1.45; }}
</style>
</head>
<body>
<div id="{map_id}">
  <div class="facility-legend">
    <span>🚰 음수대</span><span>🚻 화장실</span><span>🏪 편의점</span><span>🚇 지하철</span><span>☕ 카페</span><span>💊 약국</span><span>🏥 병원</span>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const map = L.map('{map_id}', {{ zoomControl: true, preferCanvas: true }});
map.createPane('routePane');
map.getPane('routePane').style.zIndex = 650;
map.getPane('routePane').style.pointerEvents = 'none';

L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }}).addTo(map);

const routeLines = {route_json};
const facilities = {facility_json};
const startPt = {start_json};
const endPt = {end_json};
const userPt = {user_json};

const routeGroup = L.featureGroup().addTo(map);

routeLines.forEach(line => {{
  L.polyline(line, {{ color:'#ffffff', weight:10, opacity:0.95, pane:'routePane', lineCap:'round', lineJoin:'round' }}).addTo(routeGroup);
  L.polyline(line, {{ color:'#e53935', weight:6, opacity:1.0, pane:'routePane', lineCap:'round', lineJoin:'round' }}).addTo(routeGroup);
}});

const startIcon = L.divIcon({{ className:'', html:'<div class="start-icon"></div>', iconSize:[20,20], iconAnchor:[10,10] }});
const endIcon = L.divIcon({{ className:'', html:'<div class="end-icon"></div>', iconSize:[20,20], iconAnchor:[10,10] }});

L.marker(startPt, {{icon:startIcon, zIndexOffset:1200}}).bindPopup('<b>🟢 시작점</b>').addTo(map);
L.marker(endPt, {{icon:endIcon, zIndexOffset:1200}}).bindPopup('<b>🟠 종료점</b>').addTo(map);
if (userPt) {{
  const userIcon = L.divIcon({{className:'', html:'<div class="user-icon"></div>', iconSize:[20,20], iconAnchor:[10,10]}});
  L.marker(userPt, {{icon:userIcon, zIndexOffset:1400}}).bindPopup('<b>📍 내 현재 위치</b>').addTo(map);
  L.polyline([userPt, startPt], {{color:'#1976d2', weight:3, opacity:0.75, dashArray:'8 7'}}).addTo(map);
}}

function esc(s) {{ return String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'); }}

const facilityCluster = L.markerClusterGroup({{ showCoverageOnHover: false, spiderfyOnMaxZoom: true, disableClusteringAtZoom: 17, maxClusterRadius: 32, removeOutsideVisibleBounds: true }});

facilities.forEach(f => {{
  const icon = L.divIcon({{ className:'', html:`<div class="facility-icon">${{esc(f.icon)}}</div>`, iconSize:[22,22], iconAnchor:[11,11], popupAnchor:[0,-10] }});
  const address = f.address ? `<br>${{esc(f.address)}}` : '';
  const popup = `<b>${{esc(f.icon)}} ${{esc(f.category)}} · ${{esc(f.name)}}</b><br>추천 경로에서 약 ${{f.distance}}m${{address}}`;
  const marker = L.marker([f.lat, f.lon], {{ icon: icon, zIndexOffset: 500, riseOnHover: true }}).bindPopup(popup);
  facilityCluster.addLayer(marker);
}});
map.addLayer(facilityCluster);

if (routeGroup.getLayers().length > 0) {{
  const bounds = routeGroup.getBounds();
  if (userPt) bounds.extend(userPt);
  map.fitBounds(bounds, {{ padding:[24,24], maxZoom:16 }});
}} else {{ map.setView(userPt || startPt, 13); }}
</script>
</body>
</html>
"""
    return html_code, facilities


with st.sidebar:
    st.subheader("연결 상태")
    st.write("LLM 의도분석:", "OpenAI 키 설정 ✅" if OPENAI_API_KEY else "키워드 분석 🟡")
    ks = kakao_status_cached() if KAKAO_REST_API_KEY else "missing"
    st.write("Kakao 주변시설:", status_label(ks))
    if ks == "unauthorized":
        st.error("Kakao REST API 키 인증이 실패했습니다. JavaScript/Native 키가 아니라 REST API 키인지 확인하세요.")
    st.write("서울 실시간 API:", "키 설정 ✅" if SEOUL_API_KEY else "키 없음 🟡")
    st.write("기상청 실시간 날씨:", "키 설정 ✅" if KMA_API_KEY else "키 없음 🟡")
    st.caption("음수대 좌표는 Kakao와 무관하게 서울시 공원음수대 원본 데이터에서 표시합니다.")
    st.caption("지도/시설 개선: v9 · OSM + 시설 클러스터")
    if st.button("Vector DB 재생성"):
        with st.spinner("임베딩 생성 중..."):
            n = build(True)
        st.success(f"{n}개 코스 저장 완료")

    if st.session_state.logged_in_user:
        st.divider()
        st.write(f"👤 접속자: **{st.session_state.logged_in_user.get('nickname', st.session_state.logged_in_user.get('name'))}** 님")
        if st.button("🚪 로그아웃", use_container_width=True):
            st.session_state.logged_in_user = None
            st.session_state.page = "login"
            st.rerun()


# ==========================================
# 모바일 앱 스타일 UI
# ==========================================
st.markdown("""
    <style>
    .block-container { 
        max-width: 480px; 
        padding-top: 3.5rem !important; 
        margin: 0 auto; 
    }
    div[data-testid="stRadio"] > div { flex-direction: row; flex-wrap: wrap; gap: 8px; }
    div.stButton > button { font-size: 17px; font-weight: bold; padding: 13px; }
    
    h1 {
        font-size: 24px !important;
        white-space: nowrap !important;
        word-break: keep-all !important;
        text-align: center !important;
        margin-bottom: 1.2rem !important;
        line-height: 1.3 !important;
    }

    .welcome-card {
        background: linear-gradient(135deg, #1e2640 0%, #2a3b5c 100%);
        color: white;
        border-radius: 16px;
        padding: 18px 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.12);
    }
    .welcome-card-title {
        font-size: 18px;
        font-weight: bold;
        margin-bottom: 6px;
        color: #ffffff;
        word-break: keep-all;
    }
    .welcome-card-sub {
        font-size: 13px;
        color: #d1d8e6;
        line-height: 1.4;
        word-break: keep-all;
    }
    </style>
""", unsafe_allow_html=True)


# ==========================================
# 화면 A: 로그인 페이지
# ==========================================
if st.session_state.page == "login":
    st.title("🏃 RunMood Seoul 로그인")

    login_id = st.text_input("아이디", placeholder="아이디를 입력하세요", key="log_id")
    login_pw = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요", key="log_pw")

    col_login, col_signup_nav = st.columns(2)
    with col_login:
        if st.button("🔑 로그인", type="primary", use_container_width=True):
            clean_lid = login_id.strip()
            user = authenticate_user(clean_lid, login_pw)
            if user:
                st.session_state.logged_in_user = dict(user)
                st.session_state.page = "home"
                st.rerun()
            else:
                st.error("아이디 또는 비밀번호가 일치하지 않습니다.")

    with col_signup_nav:
        if st.button("📝 회원가입", use_container_width=True):
            st.session_state.id_check_msg = None
            st.session_state.nick_check_msg = None
            st.session_state.pw_check_msg = None
            st.session_state.id_checked = False
            st.session_state.nick_checked = False
            st.session_state.pw_checked = False
            st.session_state.reg_phone = ""
            st.session_state.page = "signup"
            st.rerun()

    st.caption("기본 테스트 계정: 아이디 `runner` / 비밀번호 `123`")


# ==========================================
# 화면 B: 회원가입 페이지
# ==========================================
elif st.session_state.page == "signup":
    st.title("🏃 RunMood Seoul 가입")

    # 1. 아이디 입력 및 중복확인
    c_id_in, c_id_btn = st.columns([3, 1.2])
    with c_id_in:
        v_id = st.text_input("아이디", placeholder="runner_smart", key="reg_id")
    with c_id_btn:
        st.write("")
        st.write("")
        if st.button("중복확인", key="btn_check_id", use_container_width=True):
            clean_id = v_id.strip()
            if not clean_id:
                st.session_state.id_check_msg = ("warning", "아이디를 입력해주세요.")
                st.session_state.id_checked = False
            elif user_exists(clean_id):
                st.session_state.id_check_msg = ("error", "이미 사용 중인 아이디입니다.")
                st.session_state.id_checked = False
            else:
                st.session_state.id_check_msg = ("success", "사용 가능한 아이디입니다!")
                st.session_state.id_checked = True
                st.session_state.checked_id_value = clean_id

    if st.session_state.id_check_msg:
        msg_type, msg_text = st.session_state.id_check_msg
        if msg_type == "success": st.success(msg_text)
        elif msg_type == "error": st.error(msg_text)
        elif msg_type == "warning": st.warning(msg_text)

    # 2. 닉네임 입력 및 중복확인
    c_nick_in, c_nick_btn = st.columns([3, 1.2])
    with c_nick_in:
        v_nick = st.text_input("닉네임", placeholder="러닝요정", key="reg_nick")
    with c_nick_btn:
        st.write("")
        st.write("")
        if st.button("중복확인", key="btn_check_nick", use_container_width=True):
            clean_nick = v_nick.strip()
            if not clean_nick:
                st.session_state.nick_check_msg = ("warning", "닉네임을 입력해주세요.")
                st.session_state.nick_checked = False
            elif nickname_exists(clean_nick):
                st.session_state.nick_check_msg = ("error", "이미 사용 중인 닉네임입니다.")
                st.session_state.nick_checked = False
            else:
                st.session_state.nick_check_msg = ("success", "사용 가능한 닉네임입니다!")
                st.session_state.nick_checked = True
                st.session_state.checked_nick_value = clean_nick

    if st.session_state.nick_check_msg:
        msg_type, msg_text = st.session_state.nick_check_msg
        if msg_type == "success": st.success(msg_text)
        elif msg_type == "error": st.error(msg_text)
        elif msg_type == "warning": st.warning(msg_text)

    # 3. 비밀번호 입력 & form을 이용한 확인 버튼 (Tab 이동 시 값 동기화 문제 해결)
    with st.form("password_verification_form", clear_on_submit=False):
        v_pw = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요", key="form_reg_pw")
        v_pw2 = st.text_input("비밀번호 재확인", type="password", placeholder="비밀번호를 한번 더 입력하세요", key="form_reg_pw2")
        
        btn_checked = st.form_submit_button("비밀번호 확인", use_container_width=True)
        if btn_checked:
            if not v_pw:
                st.session_state.pw_check_msg = ("warning", "⚠️ 비밀번호를 먼저 입력하세요.")
                st.session_state.pw_checked = False
            elif v_pw == v_pw2:
                st.session_state.pw_check_msg = ("success", "✅ 비밀번호가 일치합니다!")
                st.session_state.pw_checked = True
                st.session_state.final_pw = v_pw
            else:
                st.session_state.pw_check_msg = ("error", "❌ 비밀번호가 일치하지 않습니다!")
                st.session_state.pw_checked = False

    # 확인 결과 메시지 출력
    if st.session_state.get("pw_check_msg"):
        msg_type, msg_text = st.session_state.pw_check_msg
        color = "#198754" if msg_type == "success" else ("#ffc107" if msg_type == "warning" else "#dc3545")
        st.markdown(f"<div style='color: {color}; font-size: 14px; font-weight: bold; margin-top: -10px; margin-bottom: 15px; padding-left: 5px;'>{msg_text}</div>", unsafe_allow_html=True)

    # 4. 이름
    v_name = st.text_input("이름", placeholder="홍길동", key="reg_name")

    # 5. 핸드폰 번호
    v_phone = st.text_input(
        "핸드폰 번호",
        placeholder="010-0000-0000",
        key="reg_phone",
        on_change=on_phone_change
    )
    
    # 6. 이메일 주소
    v_email = st.text_input("이메일 주소", placeholder="runner_seoul@naver.com", key="reg_email")

    st.write("")
    
    if st.button("가입완료 및 러닝 시작", use_container_width=True, type="primary"):
        clean_id = v_id.strip()
        clean_nick = v_nick.strip()
        clean_name = v_name.strip()
        clean_email = v_email.strip()
        final_phone = format_phone_number(v_phone)
        password_to_use = st.session_state.get("final_pw", "")

        if not clean_id:
            st.error("아이디를 입력해주세요.")
        elif not st.session_state.id_checked or st.session_state.checked_id_value != clean_id:
            st.error("아이디 중복확인을 완료해주세요.")
        elif not clean_nick:
            st.error("닉네임을 입력해주세요.")
        elif not st.session_state.nick_checked or st.session_state.checked_nick_value != clean_nick:
            st.error("닉네임 중복확인을 완료해주세요.")
        elif not password_to_use:
            st.error("비밀번호를 입력하고 확인해주세요.")
        elif not st.session_state.pw_checked:
            st.error("비밀번호 확인 버튼을 눌러 일치 여부를 확인해주세요.")
        elif not clean_name:
            st.error("이름을 입력해주세요.")
        else:
            new_user = create_user(
                clean_id, password_to_use, clean_nick, clean_name, final_phone, clean_email
            )
            st.session_state.logged_in_user = dict(new_user)
            st.success(f"🎉 {clean_nick}님 환영합니다! 가입이 완료되었습니다.")
            st.session_state.page = "home"
            st.rerun()

    if st.button("⬅️ 로그인 화면으로 돌아가기", use_container_width=True):
        st.session_state.page = "login"
        st.rerun()


# ==========================================
# 화면 1: 조건 입력 홈 페이지
# ==========================================
elif st.session_state.page == "home":
    st.title("🏃 RunMood Seoul")

    nick = (st.session_state.logged_in_user.get("nickname") or st.session_state.logged_in_user.get("name", "러너")) if st.session_state.logged_in_user else "러너"
    st.markdown(f"""
        <div class="welcome-card">
            <div class="welcome-card-title">✨ {nick}님 RunMood 에 오신 것을 환영합니다!</div>
            <div class="welcome-card-sub">서울 실제 공간·보행·노면·고도·대기 데이터 기반 맞춤형 코스를 추천해드립니다.</div>
        </div>
    """, unsafe_allow_html=True)

    if st.button("🌐 'RunMood 코스공유' 커뮤니티 구경하기", use_container_width=True):
        st.session_state.page = "community"
        st.rerun()

    st.markdown("### 📍 현재 위치 기반 추천")
    use_gps = st.toggle("내 GPS 위치를 사용해 가까운 코스를 우선 추천", value=bool(st.session_state.user_location))
    if use_gps:
        if get_geolocation is None:
            st.warning("GPS 모듈이 설치되지 않았습니다. 터미널에서 `pip install -r requirements.txt`를 한 번 실행해주세요.")
        else:
            location = get_geolocation()
            if location and "error" in location:
                err = location.get("error", {})
                if err.get("code") == 1:
                    st.error("위치 권한이 거부되었습니다. 브라우저 주소창의 위치 권한을 허용해주세요.")
                else:
                    st.warning(f"GPS 위치를 가져오지 못했습니다: {err.get('message', '알 수 없는 오류')}")
            elif location and location.get("coords"):
                coords = location["coords"]
                st.session_state.user_location = {
                    "latitude": float(coords["latitude"]),
                    "longitude": float(coords["longitude"]),
                    "accuracy": float(coords.get("accuracy", 0) or 0),
                }
                acc = st.session_state.user_location.get("accuracy", 0)
                st.success(f"현재 위치 연결 완료 · GPS 정확도 약 {acc:.0f}m")
                st.caption(f"위도 {st.session_state.user_location['latitude']:.6f} · 경도 {st.session_state.user_location['longitude']:.6f}")
            else:
                st.info("브라우저에서 위치 권한 요청을 확인해주세요.")
    else:
        st.session_state.user_location = None

    # ==========================================
    # 실시간 날씨 (기상청 초단기실황)
    # ==========================================
    st.markdown("### 🌤 실시간 러닝 날씨")

    if use_gps and st.session_state.user_location:
        weather_lat = st.session_state.user_location["latitude"]
        weather_lon = st.session_state.user_location["longitude"]
        weather_basis = "현재 GPS 위치 기준"
    else:
        weather_lat = 37.5665
        weather_lon = 126.9780
        weather_basis = "서울시청 기준"

    if not KMA_API_KEY:
        st.info("기상청 API 키가 없어 실시간 날씨를 표시하지 않습니다.")
    else:
        weather = weather_cached(weather_lat, weather_lon)

        if weather.get("ok"):
            temp = weather.get("temperature_c")
            humidity = weather.get("humidity_pct")
            wind = weather.get("wind_speed_mps")
            rain = weather.get("rain_1h_mm")

            wc1, wc2 = st.columns(2)
            with wc1:
                st.metric("🌡 기온", f"{temp:.1f} ℃" if temp is not None else "정보 없음")
            with wc2:
                st.metric("💧 습도", f"{humidity:.0f} %" if humidity is not None else "정보 없음")

            wc3, wc4 = st.columns(2)
            with wc3:
                st.metric("💨 풍속", f"{wind:.1f} m/s" if wind is not None else "정보 없음")
            with wc4:
                st.metric("☔ 1시간 강수", f"{rain:.1f} mm" if rain is not None else "정보 없음")

            precipitation = weather.get("precipitation_type", "정보 없음")
            st.caption(
                f"{weather_basis} · 강수형태: {precipitation} · "
                f"기상청 초단기실황 {weather.get('base_date', '')} {weather.get('base_time', '')}"
            )

            weather_message = running_weather_message(weather)
            has_warning = (
                (rain is not None and rain > 0)
                or precipitation != "없음"
                or (temp is not None and (temp >= 30 or temp <= 0))
                or (humidity is not None and humidity >= 80)
                or (wind is not None and wind >= 8)
            )
            if has_warning:
                st.warning("🏃 " + weather_message)
            else:
                st.success("🏃 " + weather_message)
        else:
            st.warning("실시간 날씨를 불러오지 못했습니다: " + str(weather.get("error", "알 수 없는 오류")))

    st.markdown("### 👟 오늘의 러닝 조건")

    level = st.radio(
        "러닝 숙련도와 목표 거리를 선택해주세요",
        [
            "전체",
            "초급 (1.4~5km)",
            "중급 (적당히 5~10km)",
            "고급 (10~15km)",
            "전문가 (15km 이상)"
        ]
    )

    mood = st.selectbox(
        "지금 기분이나 목적이 어떠신가요?",
        ["전체", "스트레스 해소", "차분한 힐링", "에너지 발산", "생각 정리"]
    )

    environment = st.selectbox(
        "어떤 환경을 원하시나요?",
        ["전체", "탁 트인 강변 (평지)", "한강 주변 (시원한 강바람)", "나무가 많은 숲길 (그늘)", "사람이 적고 한적한 길", "화려한 도심 야경"]
    )

    needs_restroom = st.checkbox("🚻 화장실 필수")
    needs_store = st.checkbox("🏪 편의점 필수")
    needs_pharmacy = st.checkbox("💊 약국 필수")
    needs_subway = st.checkbox("🚇 지하철역 필수")

    extra_request = st.text_area(
        "추가 요청 (선택)",
        placeholder="예: 송파구에서 순환형으로 / GPS 아트형 / 야경 좋은 코스 / 다른 코스 추천",
        height=80,
        help="v21.9 자연어 추천 기능을 그대로 사용할 수 있습니다."
    )

    top_k = st.slider("추천 개수", 1, 5, 3)

    if st.button("📍 내 맞춤 코스 찾기", use_container_width=True, type="primary"):
        st.session_state.user_conditions = {
            "level": level,
            "mood": mood,
            "environment": environment,
            "needs_restroom": needs_restroom,
            "needs_store": needs_store,
            "needs_pharmacy": needs_pharmacy,
            "needs_subway": needs_subway,
            "use_gps": bool(use_gps and st.session_state.user_location),
            "user_location": st.session_state.user_location if use_gps else None,
        }

        query_parts = []

        if mood != "전체":
            query_parts.append(f"나는 지금 '{mood}' 목적이야.")
        if environment != "전체":
            query_parts.append(f"'{environment}' 환경에서 달리고 싶어.")
        if level != "전체":
            query_parts.append(f"러닝 등급은 '{level}'이야.")

        query = " ".join(query_parts)

        if not query.strip():
            query = "서울 전체 코스에서 조건 제한 없이 추천해줘."

        if needs_restroom:
            query += " 중간에 꼭 화장실을 들러야 해."
        if needs_store:
            query += " 물을 마셔야 하니 편의점도 근처에 있었으면 좋겠어."
        if needs_pharmacy:
            query += " 만약을 대비해 약국도 주변에 있어야 해."
        if needs_subway:
            query += " 대중교통 이용을 위해 시작점이나 도착점 근처에 지하철역이 꼭 있어야 해."
        if extra_request.strip():
            query += " 추가 요청: " + extra_request.strip()

        normalized_query = " ".join(query.lower().split())
        is_ordinal = bool(re.search(r"[1-9]\d*\s*번째", normalized_query))
        excluded = [] if is_ordinal else st.session_state.get("recommendation_history", [])

        with st.spinner("RunMood가 감성·거리·안전·환경·주변시설 조건을 분석 중..."):
            intent, items, parser = recommend(
                query,
                top_k,
                excluded_course_ids=excluded,
                user_location=st.session_state.user_location if use_gps else None
            )

            if not items and excluded:
                intent, items, parser = recommend(
                    query,
                    top_k,
                    excluded_course_ids=[],
                    user_location=st.session_state.user_location if use_gps else None
                )
                st.session_state.recommendation_history = []

        if items and not is_ordinal:
            history = st.session_state.get("recommendation_history", [])
            for item in items:
                cid = str(item.get("course_id", ""))
                if cid and cid not in history:
                    history.append(cid)
            st.session_state.recommendation_history = history[-100:]

        st.session_state.last_recommendation_signature = normalized_query
        st.session_state.recommend_results = (intent, items, parser)
        st.session_state.page = "results"
        st.rerun()


# ==========================================
# 화면 2: 추천 코스 결과 페이지
# ==========================================
elif st.session_state.page == "results":
    st.title("🏃 RunMood Seoul")
    if st.button("⬅️ 처음으로 돌아가기", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()

    intent, items, parser = st.session_state.recommend_results

    with st.expander("🧠 v21.9 분석 조건 보기", expanded=False):
        st.json(intent.model_dump())
        st.caption("분석 방식: " + parser)
        if getattr(intent, "mood", None):
            st.caption("감정 해석: " + " · ".join(str(m) for m in intent.mood))

    if not items:
        st.warning(
            "현재 안전검증 코스 중 요청 조건을 만족하는 코스가 없습니다. "
            "v21.9 기준에 따라 조건 밖 코스를 억지로 추천하지 않았습니다."
        )

    for idx, r in enumerate(items, 1):
        st.divider()
        st.markdown(f"### {idx}. {r.get('course_name','코스')}")

        c1, c2 = st.columns([3, 2])
        with c1:
            full_km = float(r.get("full_distance_km", r.get("distance_km", 0)) or 0)
            display_km = float(r.get("display_distance_km", full_km) or full_km)

            run_level = r.get("running_level") or "미분류"
            run_range = r.get("running_level_range") or ""
            terrain_diff = r.get("difficulty_final", r.get("difficulty_derived", ""))
            route_type = str(r.get("route_type", "")).strip().lower()
            is_gps_art = str(r.get("is_gps_art", False)).strip().lower() in {"true", "1", "yes", "y"}
            if is_gps_art or route_type == "gps_art":
                route_label = "🔁 완전 순환형 · 🎨 GPS 아트"
            elif route_type == "loop":
                route_label = "🔁 완전 순환형"
            elif route_type == "return":
                route_label = "↩️ 출발지 복귀형"
            else:
                route_label = r.get("route_type_label") or "➡️ 편도형"

            st.write(
                f"**{r.get('district','서울')} · {full_km:.2f}km · "
                f"{route_label} · 러닝 등급 {run_level} ({run_range}) · "
                f"지형 난이도 {terrain_diff}**"
            )

            requested = r.get("requested_distance_km")
            if requested:
                gap = abs(full_km - float(requested))
                st.caption(
                    f"🎯 희망 거리 약 {float(requested):g}km 기준 근접 추천 "
                    f"· 실제 완성 코스 {full_km:.2f}km · 차이 {gap:.2f}km"
                )

            st.write(r.get("description", ""))
            if r.get("user_distance_km") is not None:
                st.success(f"📍 현재 위치에서 코스 시작점까지 약 {float(r['user_distance_km']):.2f}km")
            
            st.info("추천 이유: " + r.get("reason", ""))
            st.write("🌿 환경/분위기:", r.get("environment", ""), "/", r.get("mood_tags_derived", ""))
            st.write("📈 고도:", r.get("elevation_summary", "정보 없음"))
            st.write(f"🚶 도보 네트워크: {float(r.get('walk_network_match_ratio',0)):.1f}% ({r.get('walkability_grade','')})")
            
            surface_cov = float(r.get("surface_known_ratio", 0) or 0)
            lit_cov = float(r.get("lit_known_ratio", 0) or 0)
            if surface_cov > 0:
                st.write(f"🛣 노면: {r.get('surface_top','정보 없음')} · 포장 {float(r.get('surface_paved_ratio',0)):.1f}% · 커버리지 {surface_cov:.1f}%")
            else:
                st.write("🛣 노면: 데이터 미확인 · OSM 노면 태그 커버리지 0.0%")
            if lit_cov > 0:
                st.write(f"💡 조명: 확인구간 중 {float(r.get('lit_yes_ratio',0)):.1f}% · 커버리지 {lit_cov:.1f}%")
            else:
                st.write("💡 조명: 데이터 미확인 · OSM 조명 태그 커버리지 0.0%")

            official_toilet = int(float(r.get("toilet_count_300m", 0) or 0))
            official_water = int(float(r.get("water_count_300m", 0) or 0))
            route_water = r.get("water_facilities") or []
            kakao_facilities = r.get("facilities") or []
            kakao_toilets = [f for f in kakao_facilities if str(f.get("category", "")).strip() == "화장실"]

            if official_toilet > 0:
                if kakao_toilets:
                    st.write(f"🚻 화장실: 공식 사전분석 {official_toilet}곳 · 현재 Kakao 주변검색 {len(kakao_toilets)}곳")
                else:
                    st.write(f"🚻 화장실: 공식 사전분석 {official_toilet}곳")
            else:
                if kakao_toilets:
                    st.write(f"🚻 화장실: 현재 Kakao 주변검색 {len(kakao_toilets)}곳 · 공식 사전분석 미집계")
                else:
                    st.write("🚻 화장실: 공식 사전분석 미집계 · 현재 Kakao 검색 결과 없음")

            actual_water_count = len(route_water) if route_water else official_water
            if actual_water_count > 0:
                st.write(f"🚰 음수대: 서울시 공원음수대 기준 경로 300m 이내 {actual_water_count}곳")
            else:
                st.write("🚰 음수대: 서울시 공원음수대 기준 경로 300m 이내 확인된 시설 없음")
            if float(r.get("live_air_pm10", 0) or 0) > 0:
                st.write(f"🌫 현재 대기질 스냅샷: PM10 {float(r.get('live_air_pm10',0)):.0f} / PM2.5 {float(r.get('live_air_pm25',0)):.0f} ㎍/㎥")
            st.caption(r.get("quality_note", ""))

        with c2:
            st.metric("추천 점수", f"{float(r.get('score',0))*100:.1f}점")
            if r.get("is_target_segment"):
                st.metric("실제 표시 거리", f"{float(r.get('display_distance_km',0)):.2f} km")
            wf = r.get("water_facilities") or []
            kf = r.get("facilities") or []
            st.write(f"**수집된 시설: 음수대 {len(wf)}개 · Kakao 시설 {len(kf)}개**")
            all_fac = wf + kf
            if all_fac:
                by_cat = {}
                for f in all_fac:
                    by_cat[f.get("category", "기타")] = by_cat.get(f.get("category", "기타"), 0) + 1
                icon_order = [("음수대", "🚰"), ("화장실", "🚻"), ("편의점", "🏪"), ("지하철역", "🚇"), ("카페", "☕"), ("약국", "💊"), ("병원", "🏥")]
                counts = [f"{ico} {cat} {by_cat.get(cat,0)}" for cat, ico in icon_order if by_cat.get(cat, 0)]
                if counts:
                    st.caption(" · ".join(counts))
            if r.get("kakao_status") == "unauthorized":
                st.error("Kakao 인증 실패: 편의점·화장실·지하철 등은 표시할 수 없습니다. REST API 키를 확인하세요.")
            elif r.get("kakao_status") not in ("ok", None):
                st.warning(f"Kakao 상태: {r.get('kakao_status')} {r.get('kakao_message','')}")

        feat = get_route_feature(r.get("course_id"))
        if feat:
            map_html, facilities = build_leaflet_map(r, feat, height=600)
            if map_html:
                components.html(map_html, height=610, scrolling=False)
            else:
                st.warning("지도에 표시할 경로 좌표가 없습니다.")
        else:
            st.warning("이 코스의 GeoJSON 경로를 찾지 못했습니다.")
            facilities = []

        st.caption("🟢 시작점 · 🟠 종료점 · 🔴 추천 경로")
        st.caption("🚰 음수대 · 🚻 화장실 · 🏪 편의점 · 🚇 지하철역 · ☕ 카페 · 💊 약국 · 🏥 병원")
        if facilities:
            with st.expander("📍 지도에 표시된 주변시설 목록", expanded=False):
                grouped = {}
                for f in facilities:
                    grouped.setdefault(f.get("category", "기타"), []).append(f)
                for category, rows in grouped.items():
                    st.markdown(f"**{category} ({len(rows)}곳)**")
                    for f in rows:
                        distance = f" · 추천 경로에서 약 {f.get('route_display_distance_m')}m"
                        address = f" · {f.get('address')}" if f.get("address") else ""
                        st.write(f"- {f.get('name','')}{distance}{address}")
        else:
            st.info("현재 추천 경로 주변에서 표시할 시설 좌표를 찾지 못했습니다.")

        if st.button(f"✅ '{r.get('course_name')}' 선택 및 공유 사이트로 이동", key=f"select_{idx}", use_container_width=True, type="primary"):
            st.session_state.selected_course = r
            st.session_state.page = "share"
            st.rerun()


# ==========================================
# 화면 3: 'RunMood 코스공유' 웹페이지
# ==========================================
elif st.session_state.page == "share":
    course = st.session_state.selected_course
    conds = st.session_state.user_conditions

    st.title("🌐 RunMood 코스공유")
    st.caption("실제 러너들과 나만의 맞춤 러닝 코스를 공유하는 전용 웹페이지입니다.")

    if st.button("⬅️ 검색 결과로 돌아가기", use_container_width=True):
        st.session_state.page = "results"
        st.rerun()

    st.divider()

    st.markdown("### 📋 내가 설정한 러닝 조건")
    st.write(f"- **숙련도/거리:** {conds.get('level', '정보 없음')}")
    st.write(f"- **기분/목적:** {conds.get('mood', '정보 없음')}")
    st.write(f"- **선호 환경:** {conds.get('environment', '정보 없음')}")

    needs = []
    if conds.get('needs_restroom'): needs.append("🚻 화장실")
    if conds.get('needs_store'): needs.append("🏪 편의점")
    if conds.get('needs_pharmacy'): needs.append("💊 약국")
    if conds.get('needs_subway'): needs.append("🚇 지하철역")
    if needs:
        st.write(f"- **필수 인프라:** {', '.join(needs)}")

    st.divider()

    st.markdown("### 🏃‍♂️ 등록할 러닝 코스 상세 정보")
    full_km = float(course.get("full_distance_km", course.get("distance_km", 0)) or 0)
    display_km = float(course.get("display_distance_km", full_km) or full_km)

    st.write(f"**코스 명칭:** {course.get('course_name')}")
    st.write(f"**위치 지역:** {course.get('district')} · **난이도:** {course.get('difficulty_final', course.get('difficulty_derived', '정보 없음'))}")
    st.write(f"**최종 거리:** {display_km:.2f} km")
    st.write(f"**코스 설명:** {course.get('description', '')}")
    st.write(f"**AI 추천 이유:** {course.get('reason', '')}")

    st.divider()

    col_cancel, col_register = st.columns(2)
    with col_cancel:
        if st.button("❌ 취소하기", use_container_width=True):
            st.session_state.page = "results"
            st.rerun()

    with col_register:
        if st.button("📝 등록하기", type="primary", use_container_width=True):
            creator = st.session_state.logged_in_user.get("nickname") or st.session_state.logged_in_user.get("name", "익명 러너") if st.session_state.logged_in_user else "익명 러너"
            add_shared_course(course, conds, display_km, creator)

            st.success("🎉 'RunMood 코스공유' 사이트에 코스가 성공적으로 등록되었습니다!")
            st.balloons()

            st.session_state.page = "community"
            st.rerun()


# ==========================================
# 화면 4: 'RunMood 코스공유' 커뮤니티 피드
# ==========================================
elif st.session_state.page == "community":
    st.title("🌐 RunMood 코스공유")
    st.caption("다른 러너들이 공유한 맞춤형 코스들을 확인해보세요!")

    if st.button("🏠 메인으로 돌아가기", use_container_width=True):
        st.session_state.page = "home"
        st.rerun()

    st.divider()

    shared_list = list_shared_courses()

    if not shared_list:
        st.info("아직 등록된 공유 코스가 없습니다. 나만의 코스를 골라 첫 번째로 등록해 보세요!")
    else:
        st.markdown(f"### 📢 총 {len(shared_list)}개의 공유된 러닝 코스")

        for idx, item in enumerate(shared_list, 1):
            c = item["course"]
            cond = item["conditions"]
            creator = item.get("creator", "익명 러너")

            with st.container():
                st.markdown(f"### ✨ {idx}. {c.get('course_name')} ({c.get('district')})")
                st.caption(f"작성자: {creator}")
                st.write(f"**거리:** {item['display_km']:.2f}km | **난이도:** {c.get('difficulty_final', '정보 없음')}")
                st.write(f"**작성자 맞춤 조건:** {cond.get('mood')} / {cond.get('environment')} / {cond.get('level')}")
                st.write(f"**코스 설명:** {c.get('description', '')}")
                st.info(f"**추천 코멘트:** {c.get('reason', '')}")

                map_toggle_key = f"show_map_{idx}"
                if map_toggle_key not in st.session_state:
                    st.session_state[map_toggle_key] = False

                if st.button(f"🗺️ 실제 코스 확인하기 ({c.get('course_name')})", key=f"btn_map_{idx}"):
                    st.session_state[map_toggle_key] = not st.session_state[map_toggle_key]

                if st.session_state[map_toggle_key]:
                    feat = get_route_feature(c.get("course_id"))
                    if feat:
                        map_html, facilities = build_leaflet_map(c, feat, height=450)
                        if map_html:
                            components.html(map_html, height=460, scrolling=False)
                        else:
                            st.warning("지도에 표시할 경로 좌표가 없습니다.")
                    else:
                        st.warning("이 코스의 GeoJSON 경로를 찾지 못했습니다.")

                col_like, col_dislike = st.columns(2)
                with col_like:
                    if st.button(f"👍 추천 ({item['likes']})", key=f"like_{idx}", use_container_width=True):
                        increment_reaction(item['id'], 'likes')
                        st.rerun()
                with col_dislike:
                    if st.button(f"👎 비추 ({item['dislikes']})", key=f"dislike_{idx}", use_container_width=True):
                        increment_reaction(item['id'], 'dislikes')
                        st.rerun()

                st.divider()