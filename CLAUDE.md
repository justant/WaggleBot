# WaggleBot — CLAUDE.md

커뮤니티 게시글 → LLM 요약 → TTS → FFmpeg → YouTube 자동 업로드 파이프라인.

---

## 상태 전이

```
COLLECTED → (수신함 승인) → EDITING → (편집실 저장/건너뛰기) → APPROVED
→ (AI워커 폴링) → PROCESSING → PREVIEW_RENDERED → RENDERED → UPLOADED
                                                              ↕
                                                    DECLINED / FAILED
```

---

## 모듈 현황

| 모듈 | 파일 |
|------|------|
| 크롤러 | `crawlers/base.py` (retry 포함), `nate_pann.py`, `bobaedream.py`, `dcinside.py`, `fmkorea.py`, `plugin_manager.py` |
| DB | `db/models.py` (Post/Comment/Content/LLMLog/ScriptData + PostStatus), `db/session.py` |
| 마이그레이션 | `db/migrations/runner.py` (schema_migrations 추적), `db/migrations/001~003.sql` |
| AI 워커 | `ai_worker/processor.py`, `ai_worker/llm/client.py`, `ai_worker/renderer/`, `ai_worker/gpu_manager.py` |
| TTS | `ai_worker/tts/` (fish_client, edge_tts, kokoro, gptsovits) |
| 자막 | `ai_worker/renderer/subtitle.py` (ASS 동적 자막) |
| 썸네일 | `ai_worker/renderer/thumbnail.py` |
| 업로더 | `uploaders/base.py` (UploaderRegistry 포함), `youtube.py`, `uploader.py` |
| 대시보드 | `dashboard.py` (수신함/편집실/갤러리/분석/설정 탭) |
| 분석 | `analytics/collector.py`, `analytics/feedback.py` (성과 기반 피드백 루프) |
| 모니터링 | `monitoring/alerting.py`, `monitoring/daemon.py` |
| 설정 | `config/settings.py` (허브), `config/crawler.py`, `config/monitoring.py` |
| 인프라 | `docker-compose.yml` (GPU), `docker-compose.galaxybook.yml` (No-GPU) |

---

## 하드 제약사항 (절대 위반 금지)

### VRAM (RTX 3080 Ti 12GB)
- **순차 처리 필수**: LLM → TTS → 렌더링
- 각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()`
- 동시 모델 로드 금지 / 4-bit 양자화 필수
- GPU 컨텍스트 매니저 필수 사용:
```python
with gpu_manager.managed_inference(ModelType.LLM, "name"):
    result = model.generate(text)
```

### FFmpeg
- GPU 환경: `h264_nvenc` 필수
- `libx264` 수동 지정 금지 (VRAM 차단)
- No-GPU 환경(galaxybook): `libx264` 자동 폴백 허용
- 프리뷰 렌더링(480x854)은 `libx264` + CPU 허용

### Docker Compose 동기화
`docker-compose.yml` 수정 시 `docker-compose.galaxybook.yml`도 반드시 동기화.

---

## 코딩 규칙

```python
# DB — 항상 with 블록
with SessionLocal() as db:
    post = db.query(Post).filter_by(status=PostStatus.APPROVED).first()
```

- **로깅**: `logging.getLogger(__name__)` — `print()` 금지
- **임포트**: 절대경로 (`from db.models import Post`)
- **경로**: `pathlib.Path` 필수 — `os.path` 금지
- **설정**: `config/settings.py` (또는 서브모듈 `config/crawler.py`, `config/monitoring.py`) 경유 — 로직 파일 내 `os.getenv()` 금지
- **타입힌트**: 모든 함수 필수
- **가드절**: 조기 반환으로 중첩 최소화

---

## 승인 없이 수정 금지

| 대상 | 이유 |
|------|------|
| `db/models.py`의 테이블 컬럼 | 스키마 변경 시 DB 마이그레이션 필요 |
| `.env` | 시크릿 포함, 커밋 절대 금지 |
| `docker-compose.yml` | GPU 매핑 민감 |
| `requirements.txt` | 의존성 충돌 위험 |
| `h264_nvenc` → `libx264` 변경 | VRAM 차단 |
| `git push --force` to main | 이력 파괴 |
| `DROP TABLE` / `/app/media/` 삭제 | 데이터 손실 |

---

## 주요 아키텍처 메모

- **대본 구조**: `db/models.py`의 `ScriptData` dataclass. `Content.summary_text`에 JSON으로 저장. 문자열이면 레거시.
- **ScriptData 위치**: `db.models.ScriptData` (정의), `ai_worker.llm.ScriptData` (re-export). 새 코드는 `from db.models import ScriptData` 사용.
- **스코어링**: `Post.engagement_score` (Float) — 조회수×0.1 + 좋아요×2.0 + 댓글×1.5 + 베스트댓글공감×0.5, 6시간 반감기 감쇠.
- **BGM**: `assets/bgm/{funny,serious,shocking,heartwarming}/` 구조.
- **자막 스타일 프리셋**: `dramatic`, `casual`, `news`, `comment`
- **크롤러 플러그인**: `BaseCrawler` 상속 → `@CrawlerRegistry.register` 데코레이터 → `crawlers/__init__.py`에 import 추가. 가이드: `crawlers/ADDING_CRAWLER.md`
- **업로더 플러그인**: `BaseUploader` 상속 → `@UploaderRegistry.register` 데코레이터 → `uploaders/__init__.py`에 import 추가. 가이드: `uploaders/ADDING_UPLOADER.md`
- **Ollama 호출**: `ai_worker/llm/client.py`의 `call_ollama_raw()` 사용 — 다른 모듈에서 Ollama HTTP 직접 호출 금지.
- **설정 분리**: `config/crawler.py` (크롤러 공통), `config/monitoring.py` (알림 임계값). `config/settings.py`에서 re-export하므로 기존 import 경로 그대로 사용 가능.
- **마이그레이션**: `db/migrations/runner.py` 통합 실행, `schema_migrations` 테이블로 적용 이력 추적.
- **피드백 루프**: `analytics/feedback.py` — UPLOADED 포스트 성과 → LLM 인사이트 → `feedback_config.json` → 다음 대본 생성 시 `extra_instructions` 주입.
- **로그 조회**: `--tail` 필수 (`docker compose logs --tail 50 ai_worker`)

---

## Phase 3 개발 현황

### Phase 3A (완료)
- 쇼츠 대본 구조화 (hook/body/closer JSON) — `ai_worker/llm/client.py`
- 인기도 스코어링 (시간감쇠) — `crawlers/base.py`
- 배치 승인/거절 — `dashboard.py`
- 대본 편집실 탭 (TTS 미리듣기) — `dashboard.py`
- 자동 썸네일 생성 — `ai_worker/renderer/thumbnail.py`

### Phase 3B (완료)
- ASS 동적 자막 (fade-in, 키워드 강조) — `ai_worker/renderer/subtitle.py`
- 장면 전환 효과 (xfade) — `ai_worker/renderer/video.py`
- BGM 분위기 기반 믹싱 — `ai_worker/renderer/video.py`
- 댓글 말풍선 시각 효과 — `ai_worker/renderer/video.py`
- 프리뷰 렌더링 분리 (저해상도 CPU) — `dashboard.py`

### Phase 3C (완료)
- YouTube Analytics 수집 — `analytics/collector.py`
- 분석 대시보드 탭 — `dashboard.py`
- 자동 승인 모드 — `dashboard.py`, `ai_worker/main.py`
- 파이프라인 병렬화 (asyncio) — `ai_worker/processor.py`
- 성과 기반 피드백 루프 — `analytics/feedback.py`

### Phase Refactor (완료 — Phase 1 + 2)
- Dead code 제거 (configurable_crawler, site_loader, nate_tok 등)
- BaseCrawler 공통 헬퍼 통합 (`_get`, `_post`, `_parse_int`, `retry`)
- SECTIONS 클래스 변수로 이동 (settings.py 응집도 향상)
- DB 마이그레이션 러너 통합 (schema_migrations 추적)
- ScriptData를 db/models.py로 이동 (순환 import 제거)
- settings.py 도메인별 분리 (`config/crawler.py`, `config/monitoring.py`)
- plugin_manager 단순화 (auto_discover 제거, crawlers/__init__.py 명시적 import)
- UploaderRegistry 도입 (업로더 플러그인 구조 통일)

---

## arch/ 문서 가이드

- `arch/done/` — 완료된 과거 스펙 (참고용)
- `arch/4. llm_optimization.md` — 5-Phase 파이프라인 (미실행, `use_content_processor=false`)
- `arch/refactor_instructions.md` — Phase 1 리팩토링 지시서 (완료)
- `arch/refactor_phase2_instructions.md` — Phase 2 리팩토링 지시서 (완료)
