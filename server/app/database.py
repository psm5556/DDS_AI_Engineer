from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

# timeout: 사내 보안 프로그램이 DB 파일을 검사하는 동안 쓰기가 바로 실패하지 않고 기다린다(기본 5초면 턴 끝·출력을 잃을 수 있다)
engine = create_engine(settings.db_url, connect_args={"check_same_thread": False, "timeout": 30})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def ensure_columns():
    """create_all은 기존 테이블에 새 컬럼을 추가하지 않으므로, SQLite에서 빠진 nullable 컬럼만 추가한다(가벼운 자동 마이그레이션)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
