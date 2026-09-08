# RunMood 서울 MVP 데이터 파이프라인

## 1. 사용할 공식 데이터

### 필수
1. 서울시 둘레길 선형 위치정보 (WGS1984)
   - 서울 열린데이터광장
   - 데이터셋: OA-11986
   - 용도: 실제 러닝 코스의 경로/좌표

2. 서울시 주요 공원현황
   - 서울 열린데이터광장
   - 데이터셋: OA-394
   - 최신 파일: `서울시 주요 공원현황(2026 상반기).xlsx`
   - 용도: 공원 러닝 후보/공원 설명/시설/좌표

3. 서울시 공중화장실 위치정보
   - 서울 열린데이터광장
   - 데이터셋: OA-22586
   - 용도: 코스 주변 화장실

### 실시간/외부 API
4. 서울시 실시간 도시데이터
   - 데이터셋: OA-21285
   - 용도: 주요 장소 혼잡도, 인구, 날씨/환경 등
   - 서울 API 인증키 필요

5. Kakao Local API
   - 용도: 편의점(CS2), 카페(CE7), 지하철역(SW8), 약국(PM9) 검색
   - Kakao REST API 키 필요

## 2. 폴더 구조

```text
runmood_seoul_starter/
├─ data/
│  ├─ courses.csv
│  ├─ seoul_parks.xlsx
│  └─ seoul_toilets.csv
├─ output/
├─ chroma_db/
├─ runmood_pipeline.py
└─ requirements.txt
```

## 3. 코랩 설치

```python
!pip install -r requirements.txt
```

또는:

```python
!pip install pandas openpyxl requests chromadb sentence-transformers geopandas shapely
```

## 4. API Key 설정

Colab:

```python
import os
os.environ["KAKAO_REST_API_KEY"] = "카카오_REST_API_KEY"
os.environ["SEOUL_API_KEY"] = "서울_열린데이터광장_KEY"
```

키는 GitHub에 올리지 마세요.

## 5. courses.csv

처음에는 20~50개 정도의 검증된 코스만 직접 정리하는 것을 권장합니다.

주요 컬럼:

- course_id
- course_name
- district
- latitude / longitude
- distance_km
- difficulty
- terrain
- environment
- mood_tags
- elevation_gain_m
- description
- source

`mood_tags` 예:
- 힐링|조용함|야경
- 활기참|도심|퇴근러닝
- 자연|숲|트레일
- 초보|평지|편안함

## 6. 실행

```bash
python runmood_pipeline.py
```

생성 결과:

```text
output/runmood_seoul_courses.csv
chroma_db/
```

## 7. 테스트 검색

파이프라인 맨 아래 예제:

```text
퇴근 후 사람 너무 많지 않고 평지에서 5km 정도 편하게 뛰고 싶어
```

이 문장을 다국어 SentenceTransformer로 임베딩한 뒤
ChromaDB의 코스 설명과 의미 유사도 검색을 수행합니다.

## 8. 다음 개발 단계

1. 서울둘레길 SHP를 GeoPandas로 읽어 GeoJSON 변환
2. 코스 선 주변 500~700m 버퍼 생성
3. 화장실/편의점 좌표와 공간 조인
4. FastAPI `/recommend` API 작성
5. Streamlit 또는 React 지도 UI 연결
6. 서울 실시간 도시데이터의 혼잡도/날씨를 최종 랭킹에 반영
