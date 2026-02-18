"""llm_logs 테이블 생성 마이그레이션.

사용법:
    docker exec wagglebot-crawler-1 python db/migrations/run_llm_logs_migration.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from db.session import engine

SQL_FILE = Path(__file__).parent / "add_llm_logs.sql"

with engine.connect() as conn:
    sql = SQL_FILE.read_text(encoding="utf-8")
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        lines = [ln for ln in stmt.splitlines()
                 if ln.strip() and not ln.strip().startswith("--")]
        if not lines:
            continue
        conn.execute(text("\n".join(lines)))
    conn.commit()

print("✅ 마이그레이션 완료: llm_logs 테이블 생성")
