"""
RunMood Seoul MVP data pipeline
--------------------------------
1) Seoul Dulle-gil / park / toilet data load
2) Normalize columns
3) Optional Kakao nearby POI enrichment
4) Build unified CSV
5) Build ChromaDB collection with Korean multilingual embeddings

Recommended environment: Google Colab / Python 3.11+
"""

from pathlib import Path
import os
import json
import math
import pandas as pd
import requests

DATA_DIR = Path("./data")
OUT_DIR = Path("./output")
CHROMA_DIR = Path("./chroma_db")
DATA_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------
# 0. Settings
# ---------------------------------------------------------------------
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
SEOUL_API_KEY = os.getenv("SEOUL_API_KEY", "")

# Expected local files:
# data/courses.csv                 <- curated course master (required)
# data/seoul_parks.xlsx            <- Seoul major parks (optional)
# data/seoul_toilets.csv           <- Seoul public toilets (optional)
#
# For the first MVP, start from courses.csv.
# Later, add SHP/GeoJSON from Seoul Dulle-gil as route geometry.

# ---------------------------------------------------------------------
# 1. Helpers
# ---------------------------------------------------------------------
def pick_column(df, candidates, default=None):
    """Return first matching column name (case-insensitive)."""
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lookup:
            return lookup[key]
    return default

def safe_float(v):
    try:
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None

def haversine_m(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2-lat1)
    dl = math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# ---------------------------------------------------------------------
# 2. Core course data
# ---------------------------------------------------------------------
def load_courses(path=DATA_DIR / "courses.csv"):
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = [
        "course_id", "course_name", "district",
        "latitude", "longitude", "distance_km",
        "difficulty", "terrain", "environment",
        "mood_tags", "source"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"courses.csv missing columns: {missing}")

    for c in ["latitude", "longitude", "distance_km", "elevation_gain_m"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "description" not in df.columns:
        df["description"] = ""

    return df

# ---------------------------------------------------------------------
# 3. Seoul park data normalizer
# ---------------------------------------------------------------------
def load_seoul_parks(path=DATA_DIR / "seoul_parks.xlsx"):
    """
    Seoul major parks sheet column names can change.
    This normalizer tries common Korean/English aliases.
    """
    df = pd.read_excel(path)

    c_name = pick_column(df, ["공원명", "PARK_NM", "공원이름"])
    c_addr = pick_column(df, ["주소", "PARK_ADDR", "공원주소", "도로명주소"])
    c_lat  = pick_column(df, ["위도", "LAT", "LATITUDE", "Y", "YCRD"])
    c_lon  = pick_column(df, ["경도", "LON", "LNG", "LONGITUDE", "X", "XCRD"])
    c_desc = pick_column(df, ["공원개요", "PARK_OTLN", "설명", "개요"])
    c_fac  = pick_column(df, ["주요시설", "MAIN_FCLT", "시설"])

    out = pd.DataFrame()
    out["park_name"] = df[c_name].astype(str) if c_name else ""
    out["address"] = df[c_addr].astype(str) if c_addr else ""
    out["latitude"] = pd.to_numeric(df[c_lat], errors="coerce") if c_lat else None
    out["longitude"] = pd.to_numeric(df[c_lon], errors="coerce") if c_lon else None
    out["description"] = df[c_desc].fillna("").astype(str) if c_desc else ""
    out["facilities"] = df[c_fac].fillna("").astype(str) if c_fac else ""
    return out

# ---------------------------------------------------------------------
# 4. Seoul toilet data normalizer
# ---------------------------------------------------------------------
def load_seoul_toilets(path=DATA_DIR / "seoul_toilets.csv"):
    # Try common Korean encodings
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="cp949")

    c_name = pick_column(df, ["화장실명", "시설명", "명칭", "NAME"])
    c_addr = pick_column(df, ["도로명주소", "주소", "ROAD_ADDR", "ADDRESS"])
    c_lat  = pick_column(df, ["위도", "LAT", "LATITUDE", "Y"])
    c_lon  = pick_column(df, ["경도", "LON", "LNG", "LONGITUDE", "X"])
    c_open = pick_column(df, ["개방시간", "운영시간", "OPEN_TIME"])

    out = pd.DataFrame()
    out["toilet_name"] = df[c_name].astype(str) if c_name else ""
    out["address"] = df[c_addr].astype(str) if c_addr else ""
    out["latitude"] = pd.to_numeric(df[c_lat], errors="coerce") if c_lat else None
    out["longitude"] = pd.to_numeric(df[c_lon], errors="coerce") if c_lon else None
    out["open_time"] = df[c_open].fillna("").astype(str) if c_open else ""
    return out.dropna(subset=["latitude", "longitude"])

# ---------------------------------------------------------------------
# 5. Kakao Local API
# ---------------------------------------------------------------------
KAKAO_CATEGORY = {
    "convenience_store": "CS2",
    "subway": "SW8",
    "cafe": "CE7",
    "pharmacy": "PM9",
    "hospital": "HP8",
}

def kakao_category_search(lat, lon, category_code, radius=700, size=15):
    if not KAKAO_REST_API_KEY:
        return []

    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "category_group_code": category_code,
        "x": lon,
        "y": lat,
        "radius": radius,
        "size": size,
        "sort": "distance",
    }
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("documents", [])

def enrich_kakao_counts(courses, radius=700):
    rows = []
    for _, row in courses.iterrows():
        item = row.to_dict()
        lat, lon = row["latitude"], row["longitude"]

        for key, code in KAKAO_CATEGORY.items():
            try:
                places = kakao_category_search(lat, lon, code, radius=radius)
                item[f"{key}_count_{radius}m"] = len(places)
                if places:
                    item[f"nearest_{key}_name"] = places[0]["place_name"]
                    item[f"nearest_{key}_distance_m"] = places[0].get("distance", "")
            except Exception as e:
                print(f"[Kakao warning] {row['course_name']} / {key}: {e}")
                item[f"{key}_count_{radius}m"] = None
        rows.append(item)
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------
# 6. Public toilet proximity
# ---------------------------------------------------------------------
def enrich_toilet_counts(courses, toilets, radius=700):
    counts, nearest_names, nearest_distances = [], [], []

    for _, c in courses.iterrows():
        lat, lon = c["latitude"], c["longitude"]
        best_name, best_d = "", None
        count = 0

        for _, t in toilets.iterrows():
            d = haversine_m(
                lat, lon,
                safe_float(t["latitude"]),
                safe_float(t["longitude"])
            )
            if d is None:
                continue
            if d <= radius:
                count += 1
            if best_d is None or d < best_d:
                best_d = d
                best_name = t["toilet_name"]

        counts.append(count)
        nearest_names.append(best_name)
        nearest_distances.append(round(best_d) if best_d is not None else None)

    result = courses.copy()
    result[f"toilet_count_{radius}m"] = counts
    result["nearest_toilet_name"] = nearest_names
    result["nearest_toilet_distance_m"] = nearest_distances
    return result

# ---------------------------------------------------------------------
# 7. Seoul real-time city data
# ---------------------------------------------------------------------
def get_seoul_citydata(area_name):
    """
    Seoul Real-time City Data API.
    API currently returns XML in the official sample endpoint.
    This helper returns raw XML text for later parsing.
    """
    if not SEOUL_API_KEY:
        return None

    safe_area = requests.utils.quote(area_name, safe="")
    url = (
        f"http://openapi.seoul.go.kr:8088/"
        f"{SEOUL_API_KEY}/xml/citydata/1/5/{safe_area}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.text

# ---------------------------------------------------------------------
# 8. RAG document construction
# ---------------------------------------------------------------------
def build_rag_document(row):
    tags = str(row.get("mood_tags", "")).replace("|", ", ")
    return (
        f"코스명: {row.get('course_name','')}. "
        f"지역: {row.get('district','서울')}. "
        f"거리: {row.get('distance_km','')}km. "
        f"난이도: {row.get('difficulty','')}. "
        f"지형: {row.get('terrain','')}. "
        f"환경: {row.get('environment','')}. "
        f"분위기 태그: {tags}. "
        f"고도상승: {row.get('elevation_gain_m','정보없음')}m. "
        f"설명: {row.get('description','')}. "
        f"출처: {row.get('source','')}."
    )

# ---------------------------------------------------------------------
# 9. ChromaDB
# ---------------------------------------------------------------------
def build_chromadb(df):
    import chromadb
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(name="runmood_seoul_courses")

    documents = [build_rag_document(row) for _, row in df.iterrows()]
    embeddings = model.encode(
        documents,
        normalize_embeddings=True,
        show_progress_bar=True
    ).tolist()

    ids = [str(v) for v in df["course_id"].tolist()]
    metadatas = []
    for _, row in df.iterrows():
        metadata = {
            "course_name": str(row.get("course_name", "")),
            "district": str(row.get("district", "")),
            "distance_km": float(row["distance_km"]) if pd.notna(row.get("distance_km")) else 0.0,
            "difficulty": str(row.get("difficulty", "")),
            "terrain": str(row.get("terrain", "")),
            "environment": str(row.get("environment", "")),
            "latitude": float(row["latitude"]) if pd.notna(row.get("latitude")) else 0.0,
            "longitude": float(row["longitude"]) if pd.notna(row.get("longitude")) else 0.0,
            "source": str(row.get("source", "")),
        }
        metadatas.append(metadata)

    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    print(f"ChromaDB saved: {CHROMA_DIR.resolve()} / {len(ids)} courses")
    return collection, model

def search_courses(collection, model, query, n_results=3):
    q_emb = model.encode([query], normalize_embeddings=True).tolist()
    result = collection.query(
        query_embeddings=q_emb,
        n_results=n_results
    )
    return result

# ---------------------------------------------------------------------
# 10. Run all
# ---------------------------------------------------------------------
def main():
    courses = load_courses()

    toilet_path = DATA_DIR / "seoul_toilets.csv"
    if toilet_path.exists():
        toilets = load_seoul_toilets(toilet_path)
        courses = enrich_toilet_counts(courses, toilets, radius=700)

    if KAKAO_REST_API_KEY:
        courses = enrich_kakao_counts(courses, radius=700)

    out_path = OUT_DIR / "runmood_seoul_courses.csv"
    courses.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Unified CSV saved: {out_path.resolve()}")

    collection, model = build_chromadb(courses)

    # Sample query
    query = "퇴근 후 사람 너무 많지 않고 평지에서 5km 정도 편하게 뛰고 싶어"
    print("\nSample query:", query)
    print(json.dumps(
        search_courses(collection, model, query, n_results=3),
        ensure_ascii=False,
        indent=2,
        default=str
    ))

if __name__ == "__main__":
    main()
