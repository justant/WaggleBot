"""마이그레이션 실행 스크립트.

사용법:
    docker exec wagglebot-crawler-1 python db/migrations/run_migration.py
"""
import sys
from pathlib import Path

# /app 을 sys.path에 추가 (컨테이너 내 프로젝트 루트)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from db.session import engine

SQL_FILE = Path(__file__).parent / "add_variant_fields.sql"

with engine.connect() as conn:
    sql = SQL_FILE.read_text(encoding="utf-8")
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        # 주석·빈 줄 스킵
        lines = [l for l in stmt.splitlines() if l.strip() and not l.strip().startswith("--")]
        if not lines:
            continue
        clean = "\n".join(lines)
        conn.execute(text(clean))
    conn.commit()

print("✅ 마이그레이션 완료: variant_group / variant_label / variant_config 컬럼 추가")
