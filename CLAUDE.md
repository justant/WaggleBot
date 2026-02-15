# WaggleBot — 커뮤니티 쇼츠 팩토리

**AI 기반 쇼츠 영상 파이프라인:** 커뮤니티 게시글 → LLM 요약 → TTS → 영상 렌더링 → 업로드.

---

## WHAT: 기술 스택 및 아키텍처

### 핵심 스택
- **Python:** 3.12 (엄격한 타입 힌트 필수)
- **데이터베이스:** MariaDB 11.x + SQLAlchemy ORM
- **웹 UI:** Streamlit (dashboard.py)
- **영상:** FFmpeg with NVENC (RTX 3080 Ti GPU 가속)
- **오케스트레이션:** GPU 지원 Docker Compose

### 단일 노드 제약사항
**중요:** 모든 모듈은 RTX 3080 Ti (12GB VRAM)를 장착한 하나의 Windows PC에서 실행됩니다.
- 분산 워커 없음 — VRAM이 부족함
- LLM/TTS는 반드시 4-bit 양자화 사용
- OOM 크래시 방지를 위해 `with gpu_manager.managed_inference()` 사용 필수

### 디렉토리 구조
```
WaggleBot/
├── crawlers/          # 웹 스크래핑 모듈 (BaseCrawler 패턴)
│   ├── base.py        # 추상 크롤러 인터페이스
│   └── nate.py        # 네이트판 구현체
├── db/                # 데이터베이스 모델 및 세션 관리
│   ├── models.py      # SQLAlchemy Post/Comment/Content 모델
│   └── session.py     # SessionLocal 컨텍스트 매니저
├── ai_worker/         # LLM, TTS, 영상 렌더링 파이프라인
│   ├── main.py        # DB 폴링 루프 (10초 간격)
│   ├── llm.py         # EEVE-Korean-10.8B 양자화 모델
│   ├── tts.py         # Kokoro-82M / GPT-SoVITS / Edge-TTS
│   └── renderer.py    # FFmpeg 영상 생성
├── assets/            # 정적 리소스
│   └── backgrounds/   # 9:16 비율 배경 영상
├── config/            # 중앙화된 설정
│   └── settings.py    # 모든 환경 변수를 여기서 로드
├── main.py            # 크롤러 진입점
├── scheduler.py       # Cron 기반 크롤러 트리거 (1시간 간격)
└── dashboard.py       # Streamlit 관리자 UI
```

---

## WHY: 설계 결정 사항

### 왜 SQLAlchemy SessionLocal을 `with` 블록과 함께 사용하는가?
**커넥션 누수 방지.** MariaDB는 제한된 커넥션 풀을 가지고 있습니다. 항상 다음과 같이 사용하세요:
```python
with SessionLocal() as db:
    # 여기서 데이터베이스 작업 수행
```

### 왜 Redis/Queue 대신 DB 폴링을 사용하는가?
**단순성.** 단일 노드 환경에서는 메시지 브로커의 오버헤드가 불필요합니다. 하지만 AI 워커를 확장할 때 경쟁 조건(race condition) 방지를 위해 반드시 `SELECT FOR UPDATE SKIP LOCKED`를 사용해야 합니다.

### 왜 h264_nvenc 코덱을 사용하는가?
**GPU 가속.** RTX 3080 Ti는 CPU보다 20배 빠르게 인코딩할 수 있습니다. CPU 인코딩은 LLM/TTS용 VRAM을 차단합니다. 절대 libx264를 사용하지 마세요.

### 왜 LLM에 4-bit 양자화를 사용하는가?
**VRAM 제약.** 12GB는 다음을 모두 수용해야 합니다: LLM (4GB) + TTS (2GB) + FFmpeg 버퍼 (2GB) + OS 오버헤드. 8-bit 모델은 OOM 크래시를 유발합니다.

### 왜 os.path 대신 pathlib.Path를 사용하는가?
**크로스 플랫폼.** WSL (Linux)과 Windows는 경로 구분자가 다릅니다. `pathlib`은 둘 다 자동으로 처리합니다.

---

## HOW: 개발 워크플로우

### 환경 설정
```bash
# 의존성 설치
pip install -r requirements.txt

# 데이터베이스 초기화
python -c "from db.session import init_db; init_db()"

# 크롤러 1회 실행 (테스트 모드)
python main.py --once

# 스케줄된 크롤러 시작
python main.py

# 대시보드 실행
streamlit run dashboard.py
```

### 데이터베이스 작업
**항상 컨텍스트 매니저를 사용하세요:**
```python
# ✅ 올바름
with SessionLocal() as db:
    post = db.query(Post).filter_by(status='APPROVED').first()

# ❌ 틀림 - 커넥션 누수 위험
db = SessionLocal()
post = db.query(Post).first()
db.close()  # 예외 발생 시 실행되지 않을 수 있음
```

### 코딩 표준
- **데이터베이스:** 항상 `with` 블록이나 `try/finally` 안에서 `db.session.SessionLocal`을 사용할 것.
- **크롤러:** 반드시 `crawlers.base.BaseCrawler`를 상속받아야 함. `fetch_listing` 및 `parse_post` 구현 필수.
- **로깅:** `logging.getLogger(__name__)` 사용. `print()` 사용 금지.
- **타입 힌트:** 모든 함수 시그니처에 필수 적용 (예: `def func(x: int) -> str:`). 복잡한 데이터 구조에는 `TypedDict`나 `dataclasses` 사용.
- **임포트:** 절대 경로 임포트 권장 (예: `from db.models import Post`).
- **파일 경로:** `os.path` 대신 `pathlib.Path` 사용 (WSL/Windows 크로스 플랫폼 호환성에 필수).
- **설정:** 모든 설정값은 `config/settings.py`에 중앙화할 것. 로직 파일 내 `os.getenv` 사용 금지.
- **스타일:** 들여쓰기를 줄이기 위해 가드 클로즈(Early Return) 패턴 사용.

## 토큰 절약 및 소통 규칙
- **간결하게:** 코드나 핵심 설명만 출력. 대화형 추임새 금지.
- **변경 사항만:** 파일 수정 시 전체가 아닌 변경된 라인(Diff)만 출력.
- **반복 금지:** `dev_spec.md` 내용 반복 금지.
- **주도적 제안:** 누락된 파일은 명세서 기반으로 생성 제안.
- **단순 작업 계획 생략:** 간단한 수정은 계획 설명 없이 즉시 실행.
- **도구 중계 금지:** "ls를 실행하겠습니다" 같은 예고 금지. 그냥 실행할 것.
- **명령어 체이닝:** 쉘 명령어는 가능하면 `&&`로 묶어서 한 번에 실행.
- **성공 시 침묵:** 단계가 성공하면 "성공했습니다" 보고 없이 즉시 다음 단계로 진행.
- **자동 push 금지:** commit까지는 자동으로 하고, push는 사용자가 직접 리뷰하고 진행.

### Git 워크플로우
**중요:** Claude Code는 명시적으로 요청할 때만 변경사항을 커밋합니다. 승인 없이는 절대 원격 저장소에 푸시하지 않습니다.

커밋을 요청하면:
1. 관련 변경사항을 스테이징
2. 설명적인 Conventional Commit 메시지 생성 (feat:/fix:/docs:)
3. 기여자 정보와 함께 `git commit` 실행
4. **중단 — 자동으로 푸시하지 않음**

푸시하려면 명시적으로 다음과 같이 말해야 합니다: *"origin에 푸시해줘"* 또는 *"PR 만들어줘"*

**브랜치 전략:**
- `main` — 프로덕션 준비 코드
- `feature/*` — 새 기능
- `fix/*` — 버그 수정

---

## 중요: AI 워커 VRAM 관리

**문제:** LLM, TTS, FFmpeg가 12GB VRAM을 두고 경쟁 → OOM 크래시 발생.

**해결책:** 명시적인 메모리 정리와 함께 순차 처리:

```python
# ai_worker/main.py
import torch
import gc

class AIWorker:
    def process_post(self, post_id: int):
        # 1단계: LLM 요약 (4GB VRAM)
        summary = self.run_llm(post_id)
        torch.cuda.empty_cache()
        gc.collect()
        
        # 2단계: TTS 생성 (2GB VRAM)
        audio_path = self.run_tts(summary)
        torch.cuda.empty_cache()
        gc.collect()
        
        # 3단계: 영상 렌더링 (FFmpeg는 2GB 사용)
        video_path = self.render_video(audio_path)
        
        # 데이터베이스 업데이트
        self.update_status(post_id, 'RENDERED', video_path)
```

**절대 여러 모델을 동시에 로드하지 마세요.** 다음 모델을 로드하기 전에 이전 모델을 언로드하세요.

---

## 크롤러 확장성 패턴

**현재:** 네이트판 크롤러만 존재.  
**미래:** 100개 이상의 사이트 (Reddit, Blind 등)

**아키텍처:** 플러그인 기반 레지스트리.

**새 사이트 추가 방법:**
1. `BaseCrawler`를 상속하는 `crawlers/newssite.py` 생성
2. `fetch_listing()`과 `parse_post()` 구현
3. `main.py`에 등록

---

## 대시보드 개발 (Streamlit)

**현재 기능:**
- Tab 1: 수신함 (COLLECTED 게시글 → 승인/거절)
- Tab 2: 진행 상태 (PROCESSING → RENDERED → UPLOADED)
- Tab 3: 갤러리 (렌더링된 영상 재생)

**UX 개선 예정:**
- 30초마다 자동 새로고침 (`st_autorefresh`)
- 필터: 사이트, 이미지 유무, 정렬 기준
- 실시간 로그 스트리밍
- 일괄 작업 (여러 게시글 동시 승인)

**파일 접근:** 영상은 `/app/media/` Docker 볼륨에 저장되며, 공유 마운트를 통해 접근 가능.

---

## 테스트 가이드라인

**단위 테스트:**
```bash
pytest tests/test_crawlers.py -v
pytest tests/test_ai_worker.py -v
```

**통합 테스트:**
```bash
# DB 연결 테스트
pytest tests/test_db_connection.py

# 엔드투엔드 파이프라인 테스트
pytest tests/test_e2e_pipeline.py
```

**테스트 통과 없이 커밋하지 마세요.** Pre-commit 훅이 이를 강제합니다.

---

## 자주 사용하는 명령어

```bash
# 데이터베이스
python -c "from db.session import init_db; init_db()"

# 크롤링
python main.py --once              # 1회 실행
python main.py                      # 스케줄 실행 (매시간)

# AI 워커
python ai_worker/main.py            # DB 폴링 루프 시작

# 대시보드
streamlit run dashboard.py

# 테스트
pytest                              # 모든 테스트
pytest -k test_crawler              # 특정 테스트
pytest --cov=crawlers               # 커버리지 포함

# Docker
docker-compose up -d                # 모든 서비스 시작
docker-compose logs -f ai_worker    # 로그 팔로우
docker-compose down                 # 서비스 중지
```

---

## 프로젝트 단계

### ✅ Phase 1 (완료)
- 크롤러 인프라 (BaseCrawler 패턴)
- 데이터베이스 스키마 (Post/Comment/Content 테이블)
- Streamlit 대시보드 (기본 수신함/갤러리)

### 🚧 Phase 2 (진행 중)
- AI 워커: LLM 요약
- TTS 생성 (Kokoro-82M)
- NVENC를 사용한 FFmpeg 영상 렌더링
- VRAM 관리 및 에러 복구

### 📋 Phase 3 (계획됨)
- 유튜브 쇼츠 업로더 (OAuth 플로우)
- 멀티 플랫폼 지원 (틱톡, 인스타그램 릴스)
- 고급 영상 효과 (Ken Burns, 전환 효과)
- 분석 대시보드

---

## 절대 수정 금지

**명시적 승인 없이 절대 수정하지 말 것:**
- `db/models.py` — 스키마 변경 시 마이그레이션 필요
- `.env` — 시크릿 포함 (절대 커밋 금지)
- `docker-compose.yml` — GPU 디바이스 매핑이 민감함
- `requirements.txt` — 의존성 충돌 위험

**위험한 작업:**
- main 브랜치에 `git push --force`
- `DROP TABLE` 쿼리
- `/app/media/` 내용 삭제 (업로드된 영상)
- `h264_nvenc`를 `libx264`로 변경 (GPU 가속 무력화)

---

## 추가 리소스

- **크롤러 패턴:** @crawlers/base.py
- **DB 모델:** @db/models.py
- **설정 참조:** @config/settings.py
- **프로젝트 명세:** @PROJECT_SPEC.md (원본 설계 문서)
