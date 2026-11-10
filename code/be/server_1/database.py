import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

RAW_DATABASE_URL = os.getenv("RAW_DATABASE_URL", "").strip()
CLEAN_DATABASE_URL = os.getenv("CLEAN_DATABASE_URL", "").strip()
USER_DATABASE_URL = os.getenv("USER_DATABASE_URL", "").strip()

def _normalize_pg_url(url: str) -> str:
    # Ep SQLAlchemy su dung driver psycopg2 de tuong thich tuyet doi voi Supabase Pooler (Port 6543/5432)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url

def get_raw_engine():
    if not RAW_DATABASE_URL:
        raise ValueError("Chua cau hinh RAW_DATABASE_URL trong file be/.env")
    return create_engine(_normalize_pg_url(RAW_DATABASE_URL), pool_pre_ping=True, pool_size=5, max_overflow=10)

def get_clean_engine():
    target_url = CLEAN_DATABASE_URL if CLEAN_DATABASE_URL else RAW_DATABASE_URL
    if not target_url:
        raise ValueError("Chua cau hinh CLEAN_DATABASE_URL trong file be/.env")
    return create_engine(_normalize_pg_url(target_url), pool_pre_ping=True, pool_size=5, max_overflow=10)

def get_user_engine():
    target_url = USER_DATABASE_URL if USER_DATABASE_URL else CLEAN_DATABASE_URL
    if not target_url:
        raise ValueError("Chua cau hinh USER_DATABASE_URL trong file be/.env")
    return create_engine(_normalize_pg_url(target_url), pool_pre_ping=True, pool_size=5, max_overflow=10)