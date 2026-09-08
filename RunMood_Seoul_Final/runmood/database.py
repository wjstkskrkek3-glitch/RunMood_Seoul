import base64
import hashlib
import hmac
import json
import os
import secrets
from contextlib import contextmanager

from .config import DATABASE_URL

PBKDF2_ITERATIONS = 210_000


def _require_url():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL이 설정되지 않았습니다. .env에 PostgreSQL 연결 문자열을 입력하세요. "
            "예: postgresql://runmood:runmood@localhost:5432/runmood"
        )


@contextmanager
def connection():
    _require_url()
    import psycopg
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    login_id VARCHAR(80) PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    nickname VARCHAR(80) UNIQUE NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    phone VARCHAR(30) DEFAULT '',
                    email VARCHAR(255) DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS course_vectors (
                    course_id VARCHAR(80) PRIMARY KEY,
                    document TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    embedding VECTOR(384) NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shared_courses (
                    id BIGSERIAL PRIMARY KEY,
                    course_id VARCHAR(80),
                    course JSONB NOT NULL,
                    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
                    display_km DOUBLE PRECISION NOT NULL DEFAULT 0,
                    creator VARCHAR(80) NOT NULL DEFAULT '익명 러너',
                    likes INTEGER NOT NULL DEFAULT 0 CHECK (likes >= 0),
                    dislikes INTEGER NOT NULL DEFAULT 0 CHECK (dislikes >= 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_shared_courses_created_at ON shared_courses(created_at DESC)")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def seed_default_user():
    if user_exists("runner"):
        return False
    create_user("runner", "123", "러닝요정", "러너", "010-1234-5678", "runner@runmood.com")
    return True


def user_exists(login_id: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE login_id=%s", (login_id.strip(),))
        return cur.fetchone() is not None


def nickname_exists(nickname: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE nickname=%s", (nickname.strip(),))
        return cur.fetchone() is not None


def create_user(login_id: str, password: str, nickname: str, name: str, phone: str = "", email: str = ""):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO users(login_id,password_hash,nickname,name,phone,email)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (login_id.strip(), _hash_password(password), nickname.strip(), name.strip(), phone, email),
        )
    return get_user(login_id)


def get_user(login_id: str):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT login_id,nickname,name,phone,email FROM users WHERE login_id=%s", (login_id.strip(),))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "nickname": row[1], "name": row[2], "phone": row[3], "email": row[4]}


def authenticate_user(login_id: str, password: str):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT login_id,password_hash,nickname,name,phone,email FROM users WHERE login_id=%s",
            (login_id.strip(),),
        )
        row = cur.fetchone()
    if not row or not _verify_password(password, row[1]):
        return None
    return {"id": row[0], "nickname": row[2], "name": row[3], "phone": row[4], "email": row[5]}


def add_shared_course(course: dict, conditions: dict, display_km: float, creator: str):
    course_id = str(course.get("course_id", "")) or None
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO shared_courses(course_id,course,conditions,display_km,creator)
               VALUES(%s,%s::jsonb,%s::jsonb,%s,%s) RETURNING id""",
            (course_id, json.dumps(course, ensure_ascii=False, default=str),
             json.dumps(conditions, ensure_ascii=False, default=str), float(display_km), creator),
        )
        return cur.fetchone()[0]


def list_shared_courses():
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id,course,conditions,display_km,creator,likes,dislikes FROM shared_courses ORDER BY id ASC"
        )
        rows = cur.fetchall()
    return [
        {"id": r[0], "course": r[1], "conditions": r[2], "display_km": float(r[3]),
         "creator": r[4], "likes": int(r[5]), "dislikes": int(r[6])}
        for r in rows
    ]


def increment_reaction(shared_id: int, reaction: str):
    if reaction not in {"likes", "dislikes"}:
        raise ValueError("reaction must be likes or dislikes")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE shared_courses SET {reaction}={reaction}+1 WHERE id=%s", (int(shared_id),))
