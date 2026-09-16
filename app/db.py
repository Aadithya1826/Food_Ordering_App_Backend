import os

from sqlalchemy import create_engine, pool
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")
APP_ENV = os.getenv("APP_ENV", "development")

if not DATABASE_URL:
    if APP_ENV == "production":
        raise RuntimeError("DATABASE_URL is required in production environment")
    DATABASE_URL = "sqlite:///restaurant.db"

engine = create_engine(
    DATABASE_URL,
    poolclass=pool.QueuePool,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()