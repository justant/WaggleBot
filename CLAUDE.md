# WaggleBot — CLAUDE.md

커뮤니티 게시글 → LLM 요약 → TTS → FFmpeg → YouTube 자동 업로드 파이프라인.

---

## 상태 전이

```
COLLECTED → (수신함 승인) → EDITING → (편집실 저장/건너뛰기) → APPROVED
→ (AI워커 폴링) → PROCESSING → RENDERED → UPLOADED
                                              ↕
                                    DECLINED / FAILED
```

---

## 모듈 현황

| 모듈 | 파일 |
|------|------|
| 크롤러 | `crawlers/base.py`, `nate_pann.py`, `nate_tok.py`, `plugin_manager.py` |
| DB | `db/models.py` (Post/Comment/Content + PostStatus), `db/session.py` |
| AI 워커 | `ai_worker/processor.py`, `llm.py`, `video.py`, `gpu_manager.py` |
| TTS | `ai_worker/tts/` (edge_tts, kokoro, gptsovits) |
| 자막 | `ai_worker/subtitle.py` (ASS 동적 자막) |
| 썸네일 | `ai_worker/thumbnail.py` |
| 업로더 | `uploaders/base.py`, `youtube.py`, `uploader.py` |
| 대시보드 | `dashboard.py` (수신함/편집실/갤러리/분석/설정 탭) |
| 분석 | `analytics/collector.py` |
| 모니터링 | `monitoring/alerting.py`, `monitoring/daemon.py` |
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
- **설정**: `config/settings.py` 경유 — 로직 파일 내 `os.getenv()` 금지
- **타입힌트**: 모든 함수 필수
- **가드절**: 조기 반환으로 중첩 최소화

---

## 승인 없이 수정 금지

| 대상 | 이유 |
|------|------|
| `db/models.py` | 스키마 변경 시 DB 마이그레이션 필요 |
| `.env` | 시크릿 포함, 커밋 절대 금지 |
| `docker-compose.yml` | GPU 매핑 민감 |
| `requirements.txt` | 의존성 충돌 위험 |
| `h264_nvenc` → `libx264` 변경 | VRAM 차단 |
| `git push --force` to main | 이력 파괴 |
| `DROP TABLE` / `/app/media/` 삭제 | 데이터 손실 |

---

## Phase 3 개발 현황

### Phase 3A (완료)
- 쇼츠 대본 구조화 (hook/body/closer JSON) — `ai_worker/llm.py`
- 인기도 스코어링 (시간감쇠) — `crawlers/base.py`
- 배치 승인/거절 — `dashboard.py`
- 대본 편집실 탭 (TTS 미리듣기) — `dashboard.py`
- 자동 썸네일 생성 — `ai_worker/thumbnail.py`

### Phase 3B (완료)
- ASS 동적 자막 (fade-in, 키워드 강조) — `ai_worker/subtitle.py`
- 장면 전환 효과 (xfade) — `ai_worker/video.py`
- BGM 분위기 기반 믹싱 — `ai_worker/video.py`, `llm.py`
- 댓글 말풍선 시각 효과 — `ai_worker/video.py`
- 프리뷰 렌더링 분리 (저해상도 CPU) — `dashboard.py`

### Phase 3C (완료)
- YouTube Analytics 수집 — `analytics/collector.py`
- 분석 대시보드 탭 — `dashboard.py`
- 자동 승인 모드 — `dashboard.py`, `ai_worker/main.py`
- 파이프라인 병렬화 (asyncio) — `ai_worker/processor.py`

---

## 주요 아키텍처 메모

- **대본 구조**: `Content.summary_text`에 JSON 저장 (`hook`/`body`/`closer`/`title_suggestion`/`tags`/`mood`). 문자열이면 레거시.
- **스코어링**: `Post.engagement_score` (Float) — 조회수×0.1 + 좋아요×2.0 + 댓글×1.5 + 베스트댓글공감×0.5, 6시간 반감기 감쇠.
- **BGM**: `assets/bgm/{funny,serious,shocking,heartwarming}/` 구조.
- **자막 스타일 프리셋**: `dramatic`, `casual`, `news`, `comment`
- **업로더**: `BaseUploader` 상속 → `platform` 클래스 변수 지정 → `config/pipeline.json`의 `upload_platforms`에 추가.
- **크롤러**: `BaseCrawler` 상속 → `plugin_manager.py` 자동 등록. 가이드: `crawlers/ADDING_CRAWLER.md`
- **로그 조회**: `--tail` 필수 (`docker compose logs --tail 50 ai_worker`)
