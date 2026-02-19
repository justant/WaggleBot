# WaggleBot 구조 개선 작업 지시서

> **대상**: Claude Code
> **목적**: Dead code 제거, 크롤러 공통화, 설정 분리, DB 마이그레이션 체계화
> **필독**: 작업 전 반드시 `CLAUDE.md` 전체를 읽고 코딩 규칙을 준수할 것

---

## 작업 순서 (반드시 순서대로 진행)

1. [Task 1] Dead Code 삭제
2. [Task 2] BaseCrawler 공통 헬퍼 통합
3. [Task 3] 크롤러 섹션 설정 분리
4. [Task 4] DB 마이그레이션 러너 통합
5. [Task 5] 검증

---

## Task 1: Dead Code 삭제

### 1-1. 사용되지 않는 파일 삭제

아래 파일들은 프로젝트에서 **한 번도 실행되지 않거나**, 구현이 없는 Dead Code이다. 삭제하라.

```bash
# YAML 기반 범용 크롤러 시스템 (실제 크롤러 4개 모두 하드코딩 클래스라 사용 안 됨)
rm crawlers/configurable_crawler.py
rm crawlers/site_loader.py
rm config/sites.yaml

# 구현 없는 빈 크롤러 (enabled=False, 메서드가 전부 return [] / log.warning)
rm crawlers/nate_tok.py
```

### 1-2. 삭제 후 참조 정리

삭제한 파일을 import하거나 참조하는 곳이 있으면 제거하라.

**확인할 파일들:**

- `crawlers/__init__.py` — `nate_tok`, `configurable_crawler`, `site_loader` import가 있으면 제거
- `crawlers/plugin_manager.py` — `auto_discover()` 메서드 내부에서 `configurable_crawler` 또는 `site_loader`를 참조하는 부분이 있으면 제거
- `main.py` (크롤러 진입점) — `site_loader.load_site_configs()` 호출이 있으면 제거
- `config/settings.py` — `NATE_TOK_SECTIONS` 같은 변수가 있으면 제거

**확인 방법:**
```bash
grep -r "configurable_crawler\|site_loader\|nate_tok\|sites\.yaml\|load_site_configs\|ConfigurableCrawler\|SiteConfigLoader" --include="*.py" .
```

결과에 나오는 모든 참조를 제거하라. 단, 이 지시서 파일 자체나 `ADDING_CRAWLER.md`는 제외.

### 1-3. ADDING_CRAWLER.md 업데이트

`crawlers/ADDING_CRAWLER.md` 파일에 YAML 기반 크롤러 관련 언급이 있으면 제거하라.
"sites.yaml", "ConfigurableCrawler" 등의 문구를 검색해서 해당 부분을 삭제.

### 1-4. plugin_manager.py의 nate_tok 등록 제거 확인

`nate_tok.py` 삭제 후 `CrawlerRegistry`에 `nate_tok`이 남아있지 않는지 확인.
`@CrawlerRegistry.register` 데코레이터가 파일 import 시점에 실행되므로, 파일 삭제만으로 충분하다.
단, `auto_discover()`가 삭제된 파일을 import 시도하면 에러가 날 수 있으므로 확인하라.

---

## Task 2: BaseCrawler 공통 헬퍼 통합

### 2-1. 현재 문제

4개 크롤러(`nate_pann.py`, `bobaedream.py`, `dcinside.py`, `fmkorea.py`)가 아래 코드를 **각각 복사**해서 사용 중이다:

| 중복 코드 | 설명 |
|---|---|
| `self._session = requests.Session()` + `headers.update()` | 생성자에서 세션 초기화 |
| `_rotate_ua()` | User-Agent 랜덤 로테이션 |
| `_parse_int(s: str) -> int` | 문자열에서 숫자 추출 |
| `_parse_stat(text, pattern) -> int` | 정규식으로 통계 숫자 추출 |

### 2-2. BaseCrawler 수정 (`crawlers/base.py`)

`BaseCrawler` 클래스에 아래 공통 메서드와 `__init__`을 추가하라.
**기존 `run()`, `_upsert()`, `_sync_comments()`, `calculate_engagement_score()`는 절대 수정하지 말 것.**

```python
import random
import re
import requests

from config.settings import REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS

class BaseCrawler(ABC):
    site_code: str = ""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)

    # --- 공통 HTTP ---

    def _rotate_ua(self) -> None:
        """User-Agent를 랜덤으로 교체한다."""
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)

    def _get(self, url: str, **kwargs) -> requests.Response:
        """UA 로테이션 + 타임아웃이 적용된 GET 요청."""
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, url: str, **kwargs) -> requests.Response:
        """UA 로테이션 + 타임아웃이 적용된 POST 요청."""
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    # --- 공통 파싱 ---

    @staticmethod
    def _parse_int(s: str) -> int:
        """문자열에서 숫자만 추출하여 int 반환. 숫자 없으면 0."""
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0

    @staticmethod
    def _parse_stat(text: str, pattern: str) -> int:
        """정규식 패턴으로 텍스트에서 통계 숫자를 추출한다."""
        m = re.search(pattern, text)
        if not m:
            return 0
        return int(m.group(1).replace(",", ""))

    @staticmethod
    def _text(el) -> str:
        """BeautifulSoup 요소의 텍스트를 안전하게 추출한다."""
        return el.get_text(strip=True) if el else ""

    # ... 기존 abstractmethod, run(), _upsert(), _sync_comments() 유지 ...
```

**중요**: `import random`, `import re`, `import requests`와 settings import를 `base.py` 상단에 추가하라.
기존 `import hashlib`, `import logging`, `from abc import ABC, abstractmethod` 등은 유지.

### 2-3. 4개 크롤러에서 중복 코드 제거

각 크롤러 파일에서 아래 작업을 수행하라:

#### (A) 생성자 변경

**Before** (4개 파일 모두 동일 패턴):
```python
def __init__(self):
    self._session = requests.Session()
    self._session.headers.update(REQUEST_HEADERS)
```

**After**:
```python
def __init__(self) -> None:
    super().__init__()
    # 사이트 고유 헤더가 있는 경우만 추가
    # 예: self._session.headers["Referer"] = "https://www.fmkorea.com/"
```

- `nate_pann.py`: 추가 헤더 없음 → `__init__` 삭제 가능 (BaseCrawler 것 그대로 사용)
- `bobaedream.py`: 추가 헤더 없음 → `__init__` 삭제 가능
- `dcinside.py`: `Referer` 헤더 있음 → `super().__init__()` 호출 후 `self._session.headers["Referer"] = ...` 추가
- `fmkorea.py`: `Referer` 헤더 있음 → 위와 동일

#### (B) 중복 메서드 삭제

4개 파일 모두에서 아래 메서드를 **삭제**하라 (BaseCrawler에서 상속받으므로):

- `_rotate_ua()` — 4개 파일 모두 삭제
- `_parse_int()` — 4개 파일 모두 삭제 (단, `nate_pann.py`도 `@staticmethod`로 동일 로직 가지고 있음)
- `_parse_stat()` — `bobaedream.py`, `dcinside.py`, `fmkorea.py`에서 삭제
  - `nate_pann.py`의 `_parse_stat`은 시그니처가 다름 (`soup, selector, prefix` → 삭제하고 호출부 수정)

#### (C) `nate_pann.py`의 `_parse_stat` 특별 처리

`nate_pann.py`의 `_parse_stat`은 `(soup, selector, prefix)` 시그니처로 BaseCrawler 것과 다르다.
이 메서드는 **삭제하고**, 호출부를 BaseCrawler의 `_text()` + `_parse_int()` 조합으로 교체하라:

```python
# Before
views = self._parse_stat(soup, "div.post-tit-info div.info span.count", prefix="조회")

# After
views_el = soup.select_one("div.post-tit-info div.info span.count")
views = self._parse_int(self._text(views_el).replace("조회", ""))
```

#### (D) `_text()` 메서드

`nate_pann.py`에만 있던 `_text()` 정적 메서드는 BaseCrawler로 이동했으므로 `nate_pann.py`에서 삭제하라.
다른 크롤러에서도 `el.get_text(strip=True) if el else ""` 패턴이 있으면 `self._text(el)`로 교체하라.

#### (E) HTTP 요청 교체 (선택 — 안전 우선이면 스킵 가능)

각 크롤러의 HTTP 요청 패턴을 `self._get()` / `self._post()`로 교체할 수 있다.
단, 기존 크롤러에 `try/except`로 에러 처리하는 부분이 있으므로, **동작 변경 없이** 교체가 가능한 부분만 교체하라.

교체 가능한 패턴:
```python
# Before
self._rotate_ua()
resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
resp.raise_for_status()

# After
resp = self._get(url)
```

교체 **불가능**한 패턴 (try/except 안에서 raise_for_status 전에 다른 처리가 있는 경우):
```python
# 이런 경우는 그대로 둔다
try:
    self._rotate_ua()
    resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
except requests.RequestException:
    log.exception(...)
    continue  # 이 continue 때문에 단순 교체 불가
```

→ `try/except` 안에서 `self._get()`을 쓰면 동일하게 동작하므로 교체 가능. 단, `_get()`이 내부에서 `raise_for_status()`를 호출하므로 기존 `resp.raise_for_status()` 줄은 삭제해야 한다.

### 2-4. import 정리

각 크롤러 파일에서 더 이상 사용하지 않는 import를 제거하라:
- `random` — `_rotate_ua` 삭제 시 (단, 다른 곳에서 쓰는지 확인)
- `from config.settings import REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS` — BaseCrawler가 처리하므로 크롤러에서 직접 사용 안 하면 제거
  - 단, `BOBAEDREAM_SECTIONS`, `DCINSIDE_SECTIONS` 등 섹션 상수는 아직 settings에서 import해야 하므로 **Task 3 이후에 정리**

---

## Task 3: 크롤러 섹션 설정을 각 크롤러로 이동

### 3-1. 현재 문제

`config/settings.py`에 4개 크롤러의 URL 섹션이 하드코딩되어 있다:
```python
NATE_PANN_SECTIONS = [...]
BOBAEDREAM_SECTIONS = [...]
DCINSIDE_SECTIONS = [...]
FMKOREA_SECTIONS = [...]
```

새 크롤러 추가 시 `settings.py`를 수정해야 하며, 크롤러의 응집도가 낮다.

### 3-2. 각 크롤러 클래스에 `SECTIONS` 클래스 변수 추가

각 크롤러 파일에서 settings import 대신 클래스 내부에 정의하라.

**nate_pann.py:**
```python
class NatePannCrawler(BaseCrawler):
    site_code = "nate_pann"
    SECTIONS = [
        {"name": "톡톡 베스트", "url": "https://pann.nate.com/talk/ranking"},
        {"name": "톡커들의 선택", "url": "https://pann.nate.com/talk/ranking/best"},
    ]
```

**bobaedream.py:**
```python
class BobaedreamCrawler(BaseCrawler):
    site_code = "bobaedream"
    SECTIONS = [
        {"name": "자유게시판 베스트", "url": "https://m.bobaedream.co.kr/board/best/freeb"},
        {"name": "전체 베스트", "url": "https://m.bobaedream.co.kr/board/new_writing/best"},
    ]
```

**dcinside.py:**
```python
class DcInsideCrawler(BaseCrawler):
    site_code = "dcinside"
    SECTIONS = [
        {"name": "실시간 베스트 (실베)", "url": "https://gall.dcinside.com/board/lists/?id=dcbest"},
        {"name": "HIT 갤러리 (힛갤)", "url": "https://gall.dcinside.com/board/lists/?id=hit"},
    ]
```

**fmkorea.py:**
```python
class FMKoreaCrawler(BaseCrawler):
    site_code = "fmkorea"
    SECTIONS = [
        {"name": "포텐 터짐 최신순", "url": "https://www.fmkorea.com/index.php?mid=best"},
        {"name": "포텐 터짐 화제순", "url": "https://www.fmkorea.com/index.php?mid=best2&sort_index=pop&order_type=desc"},
    ]
```

### 3-3. 각 크롤러의 `fetch_listing()` 수정

각 파일에서 `for section in XXX_SECTIONS:` → `for section in self.SECTIONS:` 로 교체하라.

### 3-4. settings.py에서 섹션 상수 제거

`config/settings.py`에서 아래 4개 변수 블록을 **삭제**하라:

```python
NATE_PANN_SECTIONS = [...]
BOBAEDREAM_SECTIONS = [...]
DCINSIDE_SECTIONS = [...]
FMKOREA_SECTIONS = [...]
```

### 3-5. import 정리

각 크롤러 파일에서 더 이상 사용하지 않는 settings import를 제거하라:
```python
# 삭제 대상 (각 파일에서 해당 항목만)
from config.settings import NATE_PANN_SECTIONS   # nate_pann.py
from config.settings import BOBAEDREAM_SECTIONS   # bobaedream.py
from config.settings import DCINSIDE_SECTIONS     # dcinside.py
from config.settings import FMKOREA_SECTIONS      # fmkorea.py
```

다른 settings import (`REQUEST_TIMEOUT` 등)는 Task 2에서 BaseCrawler로 이동했으므로 크롤러에서 직접 사용 안 하면 함께 제거.

---

## Task 4: DB 마이그레이션 러너 통합

### 4-1. 현재 문제

- `db/migrations/` 에 SQL 파일 2개 + 실행 스크립트 2개가 거의 동일한 코드로 존재
- `db/migrate_001_images_contents.sql`은 `db/migrations/` 밖에 따로 존재
- 어떤 마이그레이션이 적용되었는지 추적 불가

### 4-2. 마이그레이션 파일 정리

**파일 이동 및 이름 정규화:**
```bash
# migrate_001을 migrations 디렉토리로 이동 + 네이밍 통일
mv db/migrate_001_images_contents.sql db/migrations/001_images_contents.sql
mv db/migrations/add_llm_logs.sql db/migrations/002_add_llm_logs.sql
mv db/migrations/add_variant_fields.sql db/migrations/003_add_variant_fields.sql
```

**개별 실행 스크립트 삭제:**
```bash
rm db/migrations/run_llm_logs_migration.py
rm db/migrations/run_migration.py
```

### 4-3. 통합 마이그레이션 러너 생성

`db/migrations/runner.py` 파일을 새로 생성하라:

```python
"""통합 마이그레이션 러너.

적용되지 않은 SQL 마이그레이션을 순차적으로 실행한다.
schema_migrations 테이블로 적용 이력을 추적한다.

사용법:
    docker compose exec dashboard python -m db.migrations.runner
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from db.session import engine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

MIGRATIONS_DIR = Path(__file__).parent


def _ensure_tracking_table(conn) -> None:
    """마이그레이션 추적 테이블이 없으면 생성한다."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version   VARCHAR(64)  PRIMARY KEY,
            applied_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    conn.commit()


def _get_applied(conn) -> set[str]:
    """이미 적용된 마이그레이션 버전 목록을 반환한다."""
    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _run_sql(conn, sql_text: str) -> None:
    """세미콜론으로 분리된 SQL 문을 순차 실행한다."""
    for stmt in sql_text.split(";"):
        lines = [
            ln for ln in stmt.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        if not lines:
            continue
        conn.execute(text("\n".join(lines)))


def migrate() -> None:
    """미적용 마이그레이션을 순차 실행한다."""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    if not sql_files:
        logger.info("마이그레이션 파일 없음")
        return

    with engine.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _get_applied(conn)

        for sql_file in sql_files:
            version = sql_file.stem  # e.g. "001_images_contents"

            if version in applied:
                logger.info("⏭  %s (이미 적용됨)", version)
                continue

            logger.info("▶  %s 적용 중...", version)
            sql_text = sql_file.read_text(encoding="utf-8")

            try:
                _run_sql(conn, sql_text)
                conn.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                    {"v": version},
                )
                conn.commit()
                logger.info("✅ %s 완료", version)
            except Exception as e:
                conn.rollback()
                logger.error("❌ %s 실패: %s", version, e)
                raise

    logger.info("마이그레이션 완료")


if __name__ == "__main__":
    migrate()
```

### 4-4. `__init__.py` 생성

`db/migrations/__init__.py` 파일이 없으면 빈 파일로 생성하라.

### 4-5. README 업데이트 검토

`README.md`에 DB 초기화 관련 내용이 있다. 마이그레이션 실행 명령을 추가하라:

기존 DB 초기화 섹션 아래에 추가:
```markdown
### 6. DB 마이그레이션 (스키마 변경 시)

신규 마이그레이션이 추가된 경우 아래 명령을 실행합니다.
이미 적용된 마이그레이션은 자동으로 건너뜁니다.

\```bash
docker compose exec dashboard python -m db.migrations.runner
\```
```

---

## Task 5: 검증

모든 작업 완료 후 아래 항목을 확인하라.

### 5-1. 삭제 파일 참조 검증

```bash
# 삭제된 파일을 참조하는 곳이 없어야 함
grep -r "configurable_crawler\|site_loader\|nate_tok\|sites\.yaml" --include="*.py" .
grep -r "NATE_PANN_SECTIONS\|BOBAEDREAM_SECTIONS\|DCINSIDE_SECTIONS\|FMKOREA_SECTIONS" --include="*.py" .
grep -r "run_migration\.py\|run_llm_logs_migration\.py" --include="*.py" .
```

위 명령 결과가 모두 비어있어야 한다. (이 지시서 파일과 arch/ 문서는 제외)

### 5-2. import 검증

```bash
# 각 크롤러가 정상 import 되는지 확인
python -c "from crawlers.nate_pann import NatePannCrawler; print('nate_pann OK')"
python -c "from crawlers.bobaedream import BobaedreamCrawler; print('bobaedream OK')" 2>&1 | head -3
python -c "from crawlers.dcinside import DcInsideCrawler; print('dcinside OK')" 2>&1 | head -3
python -c "from crawlers.fmkorea import FMKoreaCrawler; print('fmkorea OK')" 2>&1 | head -3
python -c "from crawlers.base import BaseCrawler; print('base OK')"
```

### 5-3. BaseCrawler 상속 검증

```bash
python -c "
from crawlers.nate_pann import NatePannCrawler
c = NatePannCrawler()
assert hasattr(c, '_get'), '_get 메서드 없음'
assert hasattr(c, '_parse_int'), '_parse_int 메서드 없음'
assert hasattr(c, '_rotate_ua'), '_rotate_ua 메서드 없음'
assert hasattr(c, '_session'), '_session 없음'
assert c._parse_int('1,234명') == 1234, '_parse_int 결과 불일치'
print('BaseCrawler 상속 검증 통과')
"
```

### 5-4. 마이그레이션 러너 검증

```bash
python -c "from db.migrations.runner import migrate; print('runner import OK')"
```

### 5-5. 파일 존재 여부 확인

```bash
# 삭제되어야 할 파일 — 모두 없어야 함
test ! -f crawlers/configurable_crawler.py && echo "PASS" || echo "FAIL: configurable_crawler.py 존재"
test ! -f crawlers/site_loader.py && echo "PASS" || echo "FAIL: site_loader.py 존재"
test ! -f config/sites.yaml && echo "PASS" || echo "FAIL: sites.yaml 존재"
test ! -f crawlers/nate_tok.py && echo "PASS" || echo "FAIL: nate_tok.py 존재"
test ! -f db/migrations/run_migration.py && echo "PASS" || echo "FAIL: run_migration.py 존재"
test ! -f db/migrations/run_llm_logs_migration.py && echo "PASS" || echo "FAIL: run_llm_logs_migration.py 존재"

# 생성/이동되어야 할 파일 — 모두 있어야 함
test -f db/migrations/runner.py && echo "PASS" || echo "FAIL: runner.py 없음"
test -f db/migrations/001_images_contents.sql && echo "PASS" || echo "FAIL: 001 없음"
test -f db/migrations/002_add_llm_logs.sql && echo "PASS" || echo "FAIL: 002 없음"
test -f db/migrations/003_add_variant_fields.sql && echo "PASS" || echo "FAIL: 003 없음"
test -f db/migrations/__init__.py && echo "PASS" || echo "FAIL: __init__.py 없음"
```

---

## 절대 수정 금지 (CLAUDE.md 준수)

| 대상 | 이유 |
|---|---|
| `db/models.py` | 스키마 변경 필요 시 별도 승인 |
| `.env` | 시크릿 포함 |
| `docker-compose.yml` | GPU 매핑 민감 |
| `docker-compose.galaxybook.yml` | 동기화 필요 |
| `requirements.txt` | 의존성 충돌 위험 |
| `h264_nvenc` 관련 코드 | VRAM 차단 |

---

## 변경 파일 요약

| 파일 | 작업 |
|---|---|
| `crawlers/configurable_crawler.py` | 🗑 삭제 |
| `crawlers/site_loader.py` | 🗑 삭제 |
| `config/sites.yaml` | 🗑 삭제 |
| `crawlers/nate_tok.py` | 🗑 삭제 |
| `db/migrations/run_migration.py` | 🗑 삭제 |
| `db/migrations/run_llm_logs_migration.py` | 🗑 삭제 |
| `db/migrate_001_images_contents.sql` | 📦 이동 → `db/migrations/001_images_contents.sql` |
| `db/migrations/add_llm_logs.sql` | 📝 이름 변경 → `002_add_llm_logs.sql` |
| `db/migrations/add_variant_fields.sql` | 📝 이름 변경 → `003_add_variant_fields.sql` |
| `crawlers/base.py` | ✏️ 공통 헬퍼 추가 |
| `crawlers/nate_pann.py` | ✏️ 중복 제거 + 섹션 이동 |
| `crawlers/bobaedream.py` | ✏️ 중복 제거 + 섹션 이동 |
| `crawlers/dcinside.py` | ✏️ 중복 제거 + 섹션 이동 |
| `crawlers/fmkorea.py` | ✏️ 중복 제거 + 섹션 이동 |
| `config/settings.py` | ✏️ 4개 섹션 상수 제거 |
| `db/migrations/runner.py` | 🆕 통합 마이그레이션 러너 |
| `db/migrations/__init__.py` | 🆕 패키지 초기화 |
| `crawlers/__init__.py` | ✏️ 참조 정리 (필요 시) |
| `main.py` | ✏️ site_loader 참조 제거 (필요 시) |
