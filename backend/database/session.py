from sqlalchemy import create_engine, inspect, text
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
    _ensure_session_columns()


def _ensure_session_columns():
    inspector = inspect(engine)
    if 'sessions' not in inspector.get_table_names():
        return
    existing = {col['name'] for col in inspector.get_columns('sessions')}
    statements = []
    if 'kind' not in existing:
        statements.append('ALTER TABLE sessions ADD COLUMN kind VARCHAR DEFAULT \'rehab\'')
    if 'intake' not in existing:
        statements.append('ALTER TABLE sessions ADD COLUMN intake JSON')
    if not statements:
        return
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


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
