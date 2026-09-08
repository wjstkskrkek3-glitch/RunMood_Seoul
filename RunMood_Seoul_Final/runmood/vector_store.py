from functools import lru_cache
import math

from .config import EMBEDDING_MODEL
from .data import load_courses, row_to_document
from .database import connection, init_schema

EMBEDDING_DIM = 384

@lru_cache(maxsize=1)
def model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


def _metadata_value(v):
    if v is None:
        return ""
    try:
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


META_FIELDS = [
    "course_name","district","latitude","longitude","distance_km","difficulty_derived",
    "difficulty_final","terrain","environment","mood_tags_derived","nearby_parks",
    "toilet_count_300m","water_count_300m","description","quality_note",
    "elevation_gain_m","max_grade_pct","walk_network_match_ratio","walkability_grade",
    "surface_known_ratio","surface_paved_ratio","surface_unpaved_ratio","surface_top",
    "lit_known_ratio","lit_yes_ratio","lit_no_ratio","foot_known_ratio","foot_allowed_ratio",
    "pedestrian_path_ratio","cycleway_ratio","steps_sample_ratio","smoothness_known_ratio",
    "smoothness_good_ratio","highway_top","osm_surface_lighting_summary",
    "realtime_area_names","nearest_realtime_area_name","live_air_pm10","live_air_pm25","live_air_cai","is_gps_art"
]


def build(reset=True):
    import json
    from pgvector.psycopg import register_vector

    init_schema()
    df = load_courses()
    records = df.to_dict("records")
    docs = [row_to_document(r) for r in records]
    embeddings = model().encode(docs, normalize_embeddings=True, show_progress_bar=True)

    with connection() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            if reset:
                cur.execute("TRUNCATE TABLE course_vectors")
            for cid, doc, emb, row in zip(df.course_id.astype(str).tolist(), docs, embeddings, records):
                meta = {k: _metadata_value(row.get(k, "")) for k in META_FIELDS if k in row}
                cur.execute(
                    """INSERT INTO course_vectors(course_id,document,metadata,embedding,updated_at)
                       VALUES(%s,%s,%s::jsonb,%s,NOW())
                       ON CONFLICT(course_id) DO UPDATE SET
                         document=EXCLUDED.document, metadata=EXCLUDED.metadata,
                         embedding=EXCLUDED.embedding, updated_at=NOW()""",
                    (cid, doc, json.dumps(meta, ensure_ascii=False), emb),
                )
    return len(df)


def count():
    init_schema()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM course_vectors")
        return int(cur.fetchone()[0])


def semantic(query, n=10):
    from pgvector.psycopg import register_vector

    init_schema()
    total = count()
    if total <= 0:
        build(reset=True)
        total = count()
    if total <= 0:
        return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    safe_n = max(1, min(int(n), total))
    emb = model().encode([query], normalize_embeddings=True)[0]
    with connection() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT course_id, metadata, (embedding <=> %s) AS distance
                   FROM course_vectors
                   ORDER BY embedding <=> %s
                   LIMIT %s""",
                (emb, emb, safe_n),
            )
            rows = cur.fetchall()
    return {
        "ids": [[r[0] for r in rows]],
        "metadatas": [[r[1] for r in rows]],
        "distances": [[float(r[2]) for r in rows]],
    }
