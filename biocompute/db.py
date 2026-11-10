from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .config import DB_URL
from .models import Base

engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite:") else {}, pool_pre_ping=True)
if DB_URL.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def sqlite_pragmas(conn, rec):
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def initialize():
    Base.metadata.create_all(engine)
