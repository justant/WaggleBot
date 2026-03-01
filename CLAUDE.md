# WaggleBot — CLAUDE.md

커뮤니티 게시글 → LLM 요약 → TTS → FFmpeg → YouTube 자동 업로드 파이프라인.

## 상태 전이

```
COLLECTED → EDITING → APPROVED → PROCESSING → PREVIEW_RENDERED → RENDERED → UPLOADED
                                                                    ↕ DECLINED / FAILED
```

## 모듈 구조

| 도메인 | 주요 파일 |
|------|------|
| 크롤러 | `crawlers/` — base.py (retry), nate_pann, bobaedream, dcinside, fmkorea, plugin_manager |
| DB | `db/models.py` (Post/Comment/Content/LLMLog/ScriptData/PostStatus), `db/session.py`, `db/migrations/` |
| AI 워커 | `ai_worker/processor.py` (루프), `main.py` (진입점), `gpu_manager.py` |
| LLM | `ai_worker/llm/client.py` (call_ollama_raw, generate_script), `llm/logger.py` |
| TTS | `ai_worker/tts/` (base, fish_client, edge_tts, kokoro, gptsovits), `tts_worker.py` |
| 렌더링 | `ai_worker/renderer/` — video.py, layout.py, subtitle.py, thumbnail.py |
| 파이프라인 | `ai_worker/pipeline/` — content_processor, scene_director, text_validator, llm_chunker, resource_analyzer |
| 업로더 | `uploaders/` — base.py (UploaderRegistry), youtube.py, uploader.py |
| 대시보드 | `dashboard.py` (수신함/편집실/갤러리/분석/설정) |
| 분석 | `analytics/collector.py`, `feedback.py` (성과→LLM 인사이트→feedback_config.json→대본 주입) |
| 모니터링 | `monitoring/alerting.py`, `daemon.py` |
| 설정 | `config/settings.py` (허브), `crawler.py`, `monitoring.py` |

## 하드 제약 (절대 위반 금지)

**VRAM (RTX 3090 24GB):** 2막 구조로 운영.
1막(LLM): qwen2.5:14b 8-bit 단독 실행(~14GB). 대본/자막/프롬프트 생성.
2막(미디어): LLM 완전 해제 후 Fish Speech 고사양(~5GB) + LTX v2(~12GB) 실행.
각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()` 유지. GPU 컨텍스트 매니저 필수.
동시 모델 로드는 총 VRAM 합계 18GB 이하일 때만 허용 (6GB 안전마진).
```python
with gpu_manager.managed_inference(ModelType.LLM, "name"):
    result = model.generate(text)
```

**FFmpeg:** `h264_nvenc` 필수. `libx264` 수동 지정 금지. 프리뷰(480x854)는 CPU 허용.

## 코딩 규칙

```python
with SessionLocal() as db:  # DB 항상 with 블록
    post = db.query(Post).filter_by(status=PostStatus.APPROVED).first()
```

- `logging.getLogger(__name__)` — print 금지
- 절대경로 import. ai_worker는 **패키지 경로**: `from ai_worker.llm.client import call_ollama_raw`
- `pathlib.Path` 필수 — os.path 금지
- 설정은 `config/` 경유 — 로직 내 `os.getenv()` 금지
- 타입힌트 모든 함수 필수, 가드절로 중첩 최소화
- Ollama HTTP 직접 호출 금지 → `ai_worker/llm/client.py`의 `call_ollama_raw()` 사용
- ScriptData는 `from db.models import ScriptData` (canonical 위치)
- 사이트 목록 하드코딩 금지 → `CrawlerRegistry.list_crawlers()` 동적 조회

## 승인 없이 수정 금지

`db/models.py` 컬럼 (마이그레이션 필요) · `.env` (시크릿) · `docker-compose.yml` (GPU 매핑) · `requirements.txt` (의존성) · `h264_nvenc→libx264` 변경 (VRAM) · `git push --force` to main · `DROP TABLE` / `/app/media/` 삭제

## 아키텍처 메모

- **ScriptData**: `Content.summary_text`에 JSON 저장. 문자열이면 레거시.
- **스코어링**: `Post.engagement_score` — 조회×0.1 + 좋아요×2.0 + 댓글×1.5 + 베스트공감×0.5, 6시간 반감기.
- **BGM**: `assets/bgm/{funny,serious,shocking,heartwarming}/`
- **자막 프리셋**: dramatic, casual, news, comment
- **플러그인**: 크롤러 `crawlers/ADDING_CRAWLER.md`, 업로더 `uploaders/ADDING_UPLOADER.md` 참조
- **설정 분리**: `config/crawler.py`, `config/monitoring.py`. settings.py에서 re-export.
- **로그**: `docker compose logs --tail 50 ai_worker`

## arch/ 문서

- `arch/done/` — 완료된 과거 스펙
- `arch/env/AGENT_TEAM.md` — Agent Team 운영 가이드 v3 (현행)

## Agent Team

[//]: # (5-Agent 체계 &#40;Team Lead 1 + Teammate 4&#41;. 상세: `arch/env/AGENT_TEAM.md`)

[//]: # (프롬프트: `.claude/prompts/` · 계약: `.claude/contracts/`)

[//]: # ()
[//]: # (### 공통 규칙)

[//]: # (- **도메인 소유권**: 디렉토리 단위. 새 파일은 해당 디렉토리 소유자에게 귀속. 상세 맵은 AGENT_TEAM.md Section 2.)

[//]: # (- **타 도메인 수정 금지** → Team Lead에게 크로스 도메인 요청 &#40;Section 4-3&#41;)

[//]: # (- **공유 파일&#40;db/, config/settings.py 등&#41;** → Write-Proposal 패턴 &#40;Section 5&#41;)

[//]: # (- **새 최상위 디렉토리** → 즉시 중단 + CEO에게 Proposal &#40;Section 6&#41;)

[//]: # (- **ai_worker/ 레거시 플랫 파일 수정 금지** → 패키지 경로&#40;llm/, tts/, renderer/, pipeline/&#41;만 수정)

[//]: # ()
[//]: # (### 필수 프로세스 &#40;절대 누락 금지&#41;)

[//]: # (1. 공유 파일 수정 시: 반드시 `/proposal` 스킬을 사용하여 승인을 요청할 것. 직접 수정 절대 금지.)

[//]: # (2. 작업 완료 시: 작업을 종료하기 전에 반드시 `_result/{작업명}.md` 형식의 결과 보고서를 작성할 것.)

[//]: # ()
[//]: # (### ⚠️ CTO 지시 대응 프로세스 &#40;Absolute Rule&#41;)

[//]: # (사용자&#40;CTO&#41;가 새로운 요구사항&#40;보통 `.md` 파일 참조 지시&#41;을 전달했을 때, **절대 코드를 즉시 수정하거나 개발을 시작하지 마십시오.**)

[//]: # (반드시 가장 먼저 `.claude/prompts/team_lead.md`를 읽고 **[Step 1] 요구사항 분석 및 검증**과 **[Step 2] 작업 실행 제안서 작성** 단계로 돌입해야 합니다.)

[//]: # (`_proposals/`에 계획을 문서화하고 승인을 받기 전까지는 코딩 도구를 사용해서는 안 됩니다.)

[//]: # ()
[//]: # (### 에이전트 도구 및 스킬)

[//]: # (- 스킬 정의: .claude/skills/)

[//]: # (- 사용법: 에이전트는 특정 작업&#40;제안서 작성 등&#41; 수행 시 해당 디렉토리의 스킬 가이드를 읽고 `/명령어` 형태로 실행한다.)