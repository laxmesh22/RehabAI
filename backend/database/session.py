from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.config import DATA_DIR, DATABASE_URL
from backend.database.models import Base

engine = None
SessionLocal = None


def rebind(url=None):
    global engine, SessionLocal
    url = url or DATABASE_URL
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connect_args = {'check_same_thread': False} if url.startswith('sqlite') else {}
    engine = create_engine(url, future=True, connect_args=connect_args)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine


rebind(DATABASE_URL)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
