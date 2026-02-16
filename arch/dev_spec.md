# WaggleBot 개발 명세서

## 1. 프로젝트 개요

인기 커뮤니티 게시글 → LLM 요약 → TTS → FFmpeg 렌더링 → YouTube 업로드 완전 자동화 파이프라인.

| 항목 | 내용 |
|------|------|
| **언어** | Python 3.12 |
| **DB** | MariaDB 11.x + SQLAlchemy ORM |
| **LLM** | Ollama (qwen2.5:14b / 1.5b) |
| **TTS** | Edge-TTS / Kokoro-82M / GPT-SoVITS |
| **영상** | FFmpeg (h264_nvenc) |
| **인프라** | Docker Compose (GPU/No-GPU 분리) |
| **GPU** | RTX 3080 Ti 12GB VRAM — 순차 처리 필수 |

---

## 2. 데이터 흐름 & 상태 전이

```
Scheduler(1hr) → Crawler → DB(COLLECTED) → Dashboard(검수)
                                                    │ APPROVED
                                                    ▼
                                           AI Worker(폴링 10초)
                                           LLM → TTS → Render
                                                    │ RENDERED
                                                    ▼
                                             Uploader(YouTube)
                                                    │ UPLOADED
```

**상태:** `COLLECTED → APPROVED → PROCESSING → RENDERED → UPLOADED / DECLINED / FAILED`

---

## 3. 완료된 모듈

### 크롤러 (`crawlers/`) ✅
- BaseCrawler 추상 클래스 + 플러그인 레지스트리 (`plugin_manager.py`)
- 구현체: `nate_pann.py`, `nate_tok.py`
- `.env`의 `ENABLED_CRAWLERS`로 활성화 제어
- 새 크롤러 추가 방법: `crawlers/ADDING_CRAWLER.md` 참조

### 데이터베이스 (`db/`) ✅
- `models.py`: Post, Comment, Content + PostStatus enum
- `session.py`: SessionLocal, init_db
- 인덱스: `(site_code, status)`, `(status, created_at)`

### 대시보드 (`dashboard.py`) ✅
- Tab 1 수신함: COLLECTED 게시글 승인/거절
- Tab 2 진행상태: PROCESSING~UPLOADED 모니터링, 파이프라인 설정
- Tab 3 갤러리: 완성 영상 재생/다운로드

### AI 워커 (`ai_worker/`) ✅
- `main.py`: APPROVED 폴링 메인 루프
- `processor.py`: LLM → TTS → Render 순차 처리 오케스트레이터
- `llm.py`: Ollama API 기반 요약 생성
- `tts/`: Edge-TTS(기본), Kokoro, GPT-SoVITS 백엔드
- `video.py`: FFmpeg subprocess 기반 9:16 렌더러 (자막 오버레이, BGM 믹스)
- `gpu_manager.py`: GPUMemoryManager — 모델별 VRAM 추적 및 컨텍스트 매니저

### 업로더 (`uploaders/`) ✅
- `base.py`: BaseUploader 추상 클래스
- `youtube.py`: YouTube Data API v3 resumable upload
- `uploader.py`: 멀티 플랫폼 디스패치 (upload_post 함수)

### 모니터링 (`monitoring/`) ✅
- `alerting.py`: CPU/GPU/디스크/DB 헬스체크, 이메일·슬랙 알림
- `daemon.py`: 백그라운드 헬스체크 데몬

### 인프라 ✅
- `docker-compose.yml` (GPU), `docker-compose.galaxybook.yml` (No-GPU)
- `Dockerfile.gpu`, `Dockerfile`, `scripts/setup_docker_gpu.sh`

---

## 4. 개발 예정 (Phase 3)

### 4.1 멀티 플랫폼 업로더

**목표:** YouTube 외 TikTok, Instagram Reels 지원

**패턴:** 기존 `BaseUploader`를 상속하면 자동으로 플랫폼 목록에 등록됨.

```python
# uploaders/tiktok.py
from uploaders.base import BaseUploader

class TikTokUploader(BaseUploader):
    platform = "tiktok"

    def validate_credentials(self) -> bool:
        ...

    def upload(self, video_path: Path, metadata: dict) -> dict:
        # TikTok Content Posting API
        # 반환: {"url": "...", "video_id": "...", "platform": "tiktok"}
        ...
```

**활성화:** `config/pipeline.json`의 `upload_platforms` 배열에 플랫폼명 추가.

```json
{"upload_platforms": "[\"youtube\", \"tiktok\"]"}
```

**주의:** 9:16 비율 영상은 이미 렌더링됨. 플랫폼별 길이 제한(TikTok 60초) 확인 필요.

---

### 4.2 고급 영상 효과

**목표:** Ken Burns 효과(이미지 팬/줌), 장면 전환, 다이나믹 자막

**구현 위치:** `ai_worker/video.py` 내부 함수 확장

**Ken Burns 구현 방법 (FFmpeg zoompan 필터):**

```python
# 이미지를 서서히 확대하며 팬
zoompan_filter = (
    "zoompan=z='min(zoom+0.0015,1.5)'"
    ":x='iw/2-(iw/zoom/2)'"
    ":y='ih/2-(ih/zoom/2)'"
    ":d=125:s=1080x1920:fps=30"
)
```

**자막 애니메이션 (fade-in per word):**
- 현재: `drawtext` 필터로 전체 문장 표시
- 목표: 단어 단위 타이밍 기반 페이드인 (TTS 타임스탬프 활용)

**전제 조건:** Edge-TTS의 `word_boundary` 콜백으로 단어별 타임스탬프 수집

---

### 4.3 분석 대시보드

**목표:** 업로드된 영상의 조회수/좋아요/댓글 트래킹, 성과 리포트

**구현 위치:** `dashboard.py`에 Tab 4 추가 또는 별도 `analytics.py`

**필요한 데이터:**
- YouTube Analytics API: 영상별 조회수, 시청 지속 시간
- DB: `contents.upload_meta`에 이미 `{"youtube": {"url": ..., "video_id": ...}}` 저장됨

**스키마 확장 없이 구현 가능** — `upload_meta` JSON에 analytics 결과 추가 저장.

```python
# 수집 주기: 업로드 후 24시간, 7일, 30일
upload_meta["youtube"]["analytics"] = {
    "collected_at": "2026-02-16T00:00:00Z",
    "views": 1234,
    "likes": 56,
    "avg_watch_pct": 72.3,
}
```

---

## 5. 핵심 제약사항 (항상 준수)

### VRAM 관리
- LLM → TTS → 렌더링 **순차 처리 필수**
- 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()`
- 동시 모델 로드 **금지**
- `gpu_manager.managed_inference()` 컨텍스트 매니저 사용

### FFmpeg
- 인코딩: `h264_nvenc` (GPU 환경), `libx264` (No-GPU 자동 폴백)
- `libx264`를 GPU 환경에서 수동 지정 **금지** (VRAM 차단)

### DB 패턴
```python
# 항상 with 블록
with SessionLocal() as db:
    post = db.query(Post).filter_by(status=PostStatus.APPROVED).first()
```

### 경로
- `pathlib.Path` 필수 (WSL/Windows 호환)
- `os.path` 사용 금지

### 설정
- 모든 설정값은 `config/settings.py` 경유
- 로직 파일 내 `os.getenv()` 직접 호출 금지

### 코딩
- 로깅: `logging.getLogger(__name__)` (print 금지)
- 타입 힌트: 모든 함수에 필수
- 임포트: 절대 경로 (`from db.models import Post`)
