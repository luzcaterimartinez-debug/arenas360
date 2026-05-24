import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

load_dotenv()


def normalize_database_url(raw_url: str) -> str:
    """Normalize PostgreSQL URLs for SQLAlchemy and cloud hosts (e.g. Railway)."""
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    # Railway and most managed Postgres require SSL from outside the platform.
    if hostname and "sslmode" not in query:
        if hostname.endswith(".railway.app") or hostname.endswith(".rlwy.net") or "railway" in hostname:
            query["sslmode"] = "require"

    return urlunparse(parsed._replace(query=urlencode(query)))


DATABASE_URL = normalize_database_url(
    os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:arenas360@127.0.0.1:5432/ArenasApp",
    )
)

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_updates() -> None:
    """Apply lightweight schema patches for existing databases."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS foto VARCHAR(500)"))


def check_database_connection() -> dict:
    """Quick connectivity check for scripts and startup diagnostics."""
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        db_name = conn.execute(text("SELECT current_database()")).scalar()
        tables = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).scalar()

    host = urlparse(DATABASE_URL).hostname or "unknown"
    return {
        "ok": True,
        "host": host,
        "database": db_name,
        "tables": tables,
        "version": version,
    }
