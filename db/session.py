from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.settings import DATABASE_URL
from db.models import Base

# MySQL 커넥션 풀 튜닝 — 백그라운드 스레드(threading.Thread) 다수 동시 접근 대응
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,           # 기본 5 → 10으로 확장
    max_overflow=20,        # 풀 초과 시 최대 추가 커넥션
    pool_timeout=30,        # 커넥션 획득 대기 최대 30초
    pool_recycle=1800,      # 30분마다 커넥션 재생성 (MySQL wait_timeout 대응)
    pool_pre_ping=True,     # 커넥션 유효성 사전 확인 (stale connection 방지)
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # commit 후 객체 속성 만료 방지
                              # detached 상태에서 selectinload된 관계 접근 시 LazyLoad 오류 제거
)


def init_db():
    Base.metadata.create_all(bind=engine)
