
## PostgreSQL + pgVector 버전

이 통합본은 추천 벡터 DB를 ChromaDB에서 PostgreSQL + pgVector로 교체했습니다. 회원정보와 커뮤니티 공유 코스도 PostgreSQL에 영구 저장됩니다. 실행 절차는 `POSTGRES_PGVECTOR_실행방법.txt`를 참고하세요.

# RunMood Seoul 통합본

이 프로젝트는 두 패키지를 합친 실행용 통합본입니다.

- 프로그램: RunMood 서울 데이터 스타터
- 실제 데이터: RunMood Seoul 실제 데이터셋

## 핵심 데이터
- `data/courses.csv`: 실제 서울 공간데이터 기반 12개 1차 러닝 구간
- `data/routes.geojson`: 위 코스들의 실제 선형 경로

> 난이도와 감성 태그는 규칙 기반 파생값이므로 발표/서비스 적용 전 검증 권장.

## 1. 설치
Python 3.11 권장

```bash
pip install -r requirements.txt
```

## 2. 환경변수
`.env.example`을 `.env`로 복사하세요.

Windows:
```bash
copy .env.example .env
```

API 키는 선택입니다. 키가 없어도 기본 추천은 동작합니다.
- `OPENAI_API_KEY`: 자연어 의도 분석 고도화
- `KAKAO_REST_API_KEY`: 편의점/지하철/카페/약국/병원 실시간 조회
- `SEOUL_API_KEY`: 향후 서울 실시간 도시데이터 연결용

## 3. Vector DB 생성
```bash
python build_db.py
```
첫 실행 때 다국어 SentenceTransformer 모델 다운로드가 필요합니다.

## 4. Streamlit 실행
```bash
streamlit run app.py
```

## 5. FastAPI 실행
```bash
uvicorn api:app --reload
```
Swagger: `http://127.0.0.1:8000/docs`

POST `/recommend`
```json
{"query":"퇴근 후 자연 많은 곳에서 8km 정도 편하게 뛰고 싶어","top_k":3}
```

## 동작 흐름
사용자 자연어 → 감정/거리/지역 분석 → PostgreSQL + pgVector 의미 검색 → 거리/지역/분위기 재랭킹 → 실제 코스 추천 → GeoJSON 경로 표시 → Kakao 주변시설

## 주의
현재 코스 12개는 서울시 SHP 선형 공간정보를 연결해 생성한 1차 분석 구간이며 최신 공식 서울둘레길 21개 코스와 1:1 동일하다고 보장하지 않습니다.

## DEM 고도 데이터 통합

이 버전에는 Copernicus GLO-30 DEM 두 타일이 포함되어 있습니다.

- `data/dem/Copernicus_DSM_COG_10_N37_00_E126_00_DEM.tif`
- `data/dem/Copernicus_DSM_COG_10_N37_00_E127_00_DEM.tif`

`data/courses.csv`에는 다음 파생 컬럼이 추가되었습니다.

- `elevation_min_m`
- `elevation_max_m`
- `elevation_avg_m`
- `elevation_gain_m`
- `elevation_loss_m`
- `avg_grade_pct`
- `max_grade_pct`
- `difficulty_elevation`
- `difficulty_final`
- `elevation_summary`

계산은 약 30m 간격으로 경로를 샘플링하고, DEM/DSM 노이즈를 줄이기 위해 중앙값 스무딩 후 2m 미만 고도 변화는 누적 상승/하강에서 제외했습니다.

> 주의: Copernicus GLO-30은 DSM이므로 건물·수목 영향이 일부 포함될 수 있습니다. `difficulty_final`은 프로젝트용 규칙 기반 파생값이며 현장 검증값은 아닙니다.



## 추가 통합 데이터 (최종본)
- 서울시 공원음수대: 코스 300m/700m 내 개수 및 최근접 음수대
- 서울시 주요 121장소 영역: 코스와 교차하는 실시간 도시데이터 AREA_CD/AREA_NM 매핑
- 서울시 2025 시간평균 대기환경: 자치구×시간대별 PM10/PM2.5/O3/NO2/CO/SO2 프로필
- 실시간 값 자체는 `SEOUL_API_KEY`로 서울 실시간 도시데이터 API를 호출해 갱신하는 구조로 확장 가능

핵심 파일: `data/courses.csv`, `data/routes.geojson`, `data/air_quality_profile_2025_by_district_hour.csv`, `data/seoul_121_realtime_areas.geojson`


## 코스 확장 데이터 (2026-08-27)

- 기존 코스: 12개
- DoDreamWay01: 기존 둘레길 원본이므로 중복 추가하지 않음
- DoDreamWay04: 지천·한강·수변길 추가
- DoDreamWay05: 한양도성 구간 추가
- 1km 미만 단편 경로 제외
- 신규 코스: 42개
- 최종 코스: 54개

신규 코스에도 DEM 고도, 누적상승, 경사, 화장실, 음수대, 공원,
서울 실시간 121장소 매칭, 2025 대기질 평균, 감성 태그를 적용했습니다.

자치구는 경로 300m 이내 공중화장실의 `구 명칭`을 우선 사용해 복수 자치구를 보존했습니다.
난이도와 감성 태그는 프로젝트용 파생값이므로 실제 서비스 전 현장 검증을 권장합니다.


## 도보 네트워크 + 실시간 대기환경 통합

서울시 자치구별 도보 네트워크를 기존 코스와 공간 매칭하여 보행 네트워크 일치율과
횡단보도·교량·터널·육교·공원녹지·건물내 링크 정보를 추가했습니다.

서울시 실시간 자치구별 대기환경 CSV의 현재 스냅샷을 자치구 기준으로 코스에 연결했습니다.
앱 운영 시에는 동일 서비스의 Open API를 호출하여 이 값을 갱신하는 방식을 권장합니다.

강남구 불법주정차 단속 CCTV 데이터는 RunMood의 안전성 평가 목적과 맞지 않아 통합하지 않았습니다.


### CCTV 제외
강남구 불법주정차 단속 CCTV 데이터는 RunMood 목적과 맞지 않아 CCTV 데이터는 통합하지 않았습니다.


## OSM 노면·야간조명 통합

OpenStreetMap `south-korea-260824.osm.pbf`를 사용해 현재 54개 코스 주변을 공간 매칭했습니다.
약 25m 간격으로 코스를 샘플링하고 30m 이내 가장 가까운 OSM highway를 연결해 다음 값을 계산합니다.

- surface: 포장/비포장 및 대표 노면
- lit: 조명 있음/없음 및 태그 커버리지
- highway: footway/path/pedestrian/cycleway/steps 등
- foot: 보행 허용/제한
- smoothness: 노면 평탄도
- tracktype: 비포장 트랙 등급

중요: OSM에서 태그가 없는 구간은 `없음`이 아니라 `정보 없음`입니다. 따라서 `surface_known_ratio`, `lit_known_ratio`를 반드시 함께 해석해야 합니다.
원본 PBF는 `data/source_osm/south-korea-260824.osm.pbf`에 보관됩니다.

# 완전 통합본 안내

이 버전은 다음 3개 계열을 하나로 합친 최신 마스터입니다.

1. `RunMood_Seoul_처음부터끝까지`: Streamlit, FastAPI, 의도분석, PostgreSQL + pgVector/RAG, Kakao 연동 코드
2. `RunMood_Seoul_OSM_최종통합본`: 54개 실제 서울 코스와 GeoJSON, DEM, 공원/화장실/음수대, 121장소, 대기질, 도보 네트워크, OSM 노면/조명
3. `RunMood_서울데이터_스타터`: 초기 데이터 수집·전처리 파이프라인 (`tools/starter_legacy/`)

## 실제 실행 데이터
- `data/courses.csv`: 최신 54개 통합 코스
- `data/routes.geojson`: 최신 54개 실제 경로
- `data/dem/`: Copernicus DEM
- `data/source_osm/`: OSM PBF 원본
- `data/source_walk_air/`: 서울 도보 네트워크 + 대기질 스냅샷

## 실행
```bash
pip install -r requirements.txt
python build_db.py
streamlit run app.py
```

FastAPI:
```bash
uvicorn api:app --reload
```
Swagger: `http://127.0.0.1:8000/docs`

API 키는 `.env.example`을 `.env`로 복사한 뒤 설정합니다. API 키가 없어도 OpenAI는 키워드 분석으로, Kakao는 주변시설 생략으로 기본 추천이 동작합니다.

## 데이터 해석 주의
- OSM `lit` 정보가 없는 구간은 `조명 없음`이 아니라 `정보 없음`입니다.
- Copernicus GLO-30은 DSM이라 수목/건물 영향이 일부 있을 수 있습니다.
- 실시간 대기질 CSV는 다운로드 시점 스냅샷이며, 운영 앱에서는 Open API 갱신을 권장합니다.
- 감성 태그와 최종 난이도는 프로젝트용 파생값이므로 현장 검증이 권장됩니다.


[2026-08-28 통합 수정]
- 지도 경로 표시 개선
- 시작/종료 마커
- 서울시 음수대 좌표 표시
- Kakao 주변시설/인증 오류 처리 개선
- 시설 검색 및 추천 로직 개선
