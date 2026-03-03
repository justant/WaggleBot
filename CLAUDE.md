# WaggleBot — CLAUDE.md

커뮤니티 게시글 → LLM 대본 → TTS → LTX-2 비디오 → FFmpeg 렌더링 → YouTube 자동 업로드 파이프라인.

## 상태 전이

```
COLLECTED → EDITING → APPROVED → PROCESSING → PREVIEW_RENDERED → RENDERED → UPLOADED
                                                                    ↕ DECLINED / FAILED
```

## 파이프라인 (8-Phase)

```
Phase 1  analyze_resources    → ResourceProfile (이미지:텍스트 비율)
Phase 2  chunk_with_llm       → raw script dict (LLM 의미 단위 청킹)
Phase 3  validate_and_fix     → validated script dict (max_chars 검증)
Phase 4  SceneDirector        → list[SceneDecision] (씬 배분 + 감정 태그)
Phase 4.5 assign_video_modes  → SceneDecision에 video_mode 설정 (t2v/i2v)
Phase 5  TTS 생성              → scene.text_lines = [{"text":..., "audio":...}]
Phase 6  video_prompt 생성     → scene.video_prompt (한국어→영어 변환)
Phase 7  video_clip 생성       → scene.video_clip_path (ComfyUI LTX-2)
Phase 8  FFmpeg 렌더링         → 최종 9:16 영상 + 썸네일
```

Phase 4.5~7은 `VIDEO_GEN_ENABLED=true`일 때만 실행.

## 모듈 구조

| 도메인 | 주요 파일 |
|------|------|
| 크롤러 | `crawlers/` — base.py (retry + 스코어링), nate_pann, bobaedream, dcinside, fmkorea, plugin_manager |
| DB | `db/models.py` (Post/Comment/Content/LLMLog/ScriptData/PostStatus), `db/session.py`, `db/migrations/` |
| AI 워커 | `ai_worker/processor.py` (루프), `ai_worker/main.py` (진입점), `gpu_manager.py` |
| LLM | `ai_worker/llm/client.py` (call_ollama_raw, generate_script), `llm/logger.py` |
| TTS | `ai_worker/tts/` (base, fish_client, edge_tts, kokoro, gptsovits) |
| 비디오 | `ai_worker/video/` — manager.py (오케스트레이션), comfy_client.py (ComfyUI 통신), prompt_engine.py (한→영 프롬프트), image_filter.py (I2V 적합성), video_utils.py (FFmpeg 후처리) |
| 렌더링 | `ai_worker/renderer/` — layout.py (하이브리드 합성), video.py (레거시/프리뷰), subtitle.py (ASS 자막), thumbnail.py |
| 파이프라인 | `ai_worker/pipeline/` — content_processor (Phase 1~8 통합), scene_director, text_validator, llm_chunker, resource_analyzer |
| 업로더 | `uploaders/` — base.py (UploaderRegistry), youtube.py, uploader.py |
| 대시보드 | `dashboard/app.py` (수신함/편집실/갤러리/분석/설정) |
| 분석 | `analytics/collector.py`, `feedback.py` (성과→LLM 인사이트→feedback_config.json→대본 주입) |
| 모니터링 | `monitoring/alerting.py`, `daemon.py` |
| 설정 | `config/settings.py` (허브), `crawler.py`, `monitoring.py`, `layout.json`, `scene_policy.json`, `video_styles.json` |

## Docker 서비스 구성

| 서비스 | 역할 | 포트 |
|--------|------|------|
| `db` | MariaDB 11 | 3306 |
| `crawler` | 크롤링 루프 | — |
| `dashboard` | Streamlit UI | 8501 |
| `fish-speech` | TTS (zero-shot 클로닝) | 8080 |
| `comfyui` | LTX-2 비디오 생성 | 8188 |
| `ai_worker` | 8-Phase 파이프라인 | — |
| `monitoring` | 헬스체크/알림 | — |

## 하드 제약 (절대 위반 금지)

**VRAM (RTX 3090 24GB):** 2막 구조로 운영.
1막(LLM): qwen2.5:14b 8-bit 단독 실행(~14GB). 대본/자막/프롬프트 생성.
2막(미디어): LLM 완전 해제 후 Fish Speech(~5GB) + LTX-2 GGUF Q4(~12.7GB, ComfyUI --lowvram) 실행.
각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()` 유지. GPU 컨텍스트 매니저 필수.
동시 모델 로드는 총 VRAM 합계 18GB 이하일 때만 허용 (6GB 안전마진).
```python
with gpu_manager.managed_inference(ModelType.LLM, "name"):
    result = model.generate(text)
```

**FFmpeg:** `h264_nvenc` 필수. `libx264` 수동 지정 금지. 프리뷰(480x854)는 CPU 허용.
**ComfyUI:** `--lowvram --reserve-vram 2` 고정. GGUF Q4 UNet(~12.7GB) + 텍스트 인코더 CPU 오프로드.
`--normalvram` 사용 금지 (텍스트 인코딩 OOM 발생). GGUF 로더는 ComfyUI-GGUF 커스텀 노드 사용.
**LTX-2 프레임 규칙:** `1+8k` (9, 17, 25, ..., 97). 해상도는 8의 배수. `video_utils.validate_frame_count()` / `validate_resolution()` 사용 필수.

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
- `ai_worker/video`는 `ai_worker/tts` 모듈을 절대 import 금지 — TTS와 비디오는 독립 파이프라인

## git / 배포 금지

작업자의 명령 없이는 자동으로 **절대 수행 금지:**
- `git commit` · `git push` · `git push --force` — 모든 git 기록 조작
- `DROP TABLE` · `/app/media/` 삭제 — 데이터 파괴

## 아키텍처 메모

- **ScriptData**: `Content.summary_text`에 JSON 저장. 문자열이면 레거시.
- **스코어링**: `Post.engagement_score` — 조회×0.1 + 좋아요×2.0 + 댓글×1.5 + 베스트공감×0.5, 6시간 반감기.
- **Mood 체계 (9종)**: humor, touching, anger, sadness, horror, info, controversy, daily, shock — `config/scene_policy.json`에서 씬 정책, `config/video_styles.json`에서 비주얼 스타일 정의.
- **BGM**: `assets/bgm/{9개 mood 카테고리}/` — scene_policy.json에서 매핑.
- **자막 프리셋**: dramatic, casual, news, comment
- **레이아웃**: `config/layout.json` (렌더러 레이아웃 Single Source of Truth)
- **비디오 출력 공유**: ComfyUI `/comfyui/output` ↔ ai_worker `media/tmp/videos` 동일 Docker 볼륨.
- **4단계 폴백 재시도**: Full(1280×720, 97f, 20step) → 프롬프트 단순화 → 해상도 다운(768×512, 65f) → Distilled(8step, CFG=1.0). 전부 실패 시 씬 삭제 + text_lines 인접 씬 병합.
- **I2V 임계값**: `VIDEO_I2V_THRESHOLD=0.6` — image_filter 점수 기준. 미만이면 T2V 전환.
- **플러그인**: 크롤러 `crawlers/ADDING_CRAWLER.md`, 업로더 `uploaders/ADDING_UPLOADER.md` 참조.
- **설정 분리**: `config/crawler.py`, `config/monitoring.py`. settings.py에서 re-export.
- **로그**: `docker compose logs --tail 50 ai_worker`

## arch/ 문서

- `arch/done/` — 완료된 과거 스펙
- `arch/env/AGENT_TEAM.md` — Agent Team 운영 가이드 v3 (현행)

## 작업 완료 보고 규칙
작업 완료 시 반드시 `_result/{작업이름}.md` 파일을 생성하여 상세 내용을 기록할 것. 작업이름은 2단어 이하.
- **필수 포함 항목:** 1. 작업 결과, 2. 수정 내용, 3. 테스트 결과물 저장 위치, 4. 수동 테스트 방법, 5. 추천 commit message
- 양식 및 작성 예시는 `_result/sample/sample.md` 파일 참조.
- _result/* 디렉토리 안에는 절대 root 권한으로 write 금지.
