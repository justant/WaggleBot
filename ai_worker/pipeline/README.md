# ai_worker/pipeline — 파이프라인 오케스트레이터

8-Phase 콘텐츠 처리 파이프라인의 통합 오케스트레이터.
개별 모듈(script, scene, tts, video, renderer)을 순차 호출하고
VRAM 2막 전환을 관리한다.

---

## 파일 구조

```
ai_worker/pipeline/
├── __init__.py            # 패키지 마커 (1줄)
└── content_processor.py   # 8-Phase 통합 오케스트레이터 (291줄)
```

---

## content_processor.py (291줄)

### process_content()

단일 콘텐츠의 전체 처리 파이프라인. `RobustProcessor`에서 호출.

```python
async def process_content(content_id: int, db: Session) -> Path:
```

### 8-Phase 실행 순서

```
Phase 1  analyze_resources()     → ResourceProfile
         ├─ 모듈: ai_worker.scene.analyzer
         └─ 이미지:텍스트 비율 분석

Phase 2  chunk_with_llm()        → raw script dict
         ├─ 모듈: ai_worker.script.chunker
         └─ LLM 의미 단위 청킹 (GPU: LLM ~14GB)

Phase 3  validate_and_fix()      → validated script dict
         ├─ 모듈: ai_worker.scene.validator
         └─ max_chars 검증 + 글자수 절삭

Phase 4  SceneDirector           → list[SceneDecision]
         ├─ 모듈: ai_worker.scene.director
         └─ 씬 배분 + 감정 태그 매핑

Phase 4.5 assign_video_modes()   → SceneDecision.video_mode
          ├─ 모듈: ai_worker.scene.director
          ├─ 조건: VIDEO_GEN_ENABLED=true
          └─ I2V/T2V 모드 할당 (이미지 적합성 평가)

─── VRAM 2막 전환 ───────────────────────
    torch.cuda.empty_cache() + gc.collect()
    emergency_cleanup() if VRAM < threshold
─────────────────────────────────────────

Phase 5  TTS 생성                → scene.text_lines[].audio
         ├─ 모듈: ai_worker.tts.fish_client
         └─ Fish Speech zero-shot 클로닝 (GPU: TTS ~5GB)

Phase 6  video_prompt 생성       → scene.video_prompt
         ├─ 모듈: ai_worker.video.prompt_engine
         ├─ 조건: VIDEO_GEN_ENABLED=true
         └─ 한국어→영어 LLM 변환 (CPU 호출)

Phase 7  video_clip 생성         → scene.video_clip_path
         ├─ 모듈: ai_worker.video.manager
         ├─ 조건: VIDEO_GEN_ENABLED=true
         └─ ComfyUI LTX-2 비디오 생성 (GPU: VIDEO ~12GB)

Phase 8  FFmpeg 렌더링           → 최종 mp4 + 썸네일
         ├─ 모듈: ai_worker.renderer.layout
         └─ 9:16 세로 영상 합성 (h264_nvenc)
```

### VRAM 2막 전환 지점

Phase 4.5 → Phase 5 사이에 GPU 메모리를 정리:

```python
# 1막 종료 (LLM 해제)
torch.cuda.empty_cache()
gc.collect()

# VRAM 여유 확인
gm = get_gpu_manager()
if gm.get_available_vram() < VIDEO_VRAM * 0.5:
    gm.emergency_cleanup()

# 2막 시작 (TTS + Video)
```

### 에러 처리

| Phase | 실패 시 |
|-------|---------|
| 1~3 | FAILED 상태 전환 + 에러 로깅 |
| 4~4.5 | FAILED 상태 전환 |
| 5 | 개별 씬 TTS 스킵 (나머지 계속) |
| 6 | 개별 씬 프롬프트 스킵 |
| 7 | 4단계 폴백 → 전부 실패 시 씬 삭제 + text_lines 병합 |
| 8 | FAILED 상태 전환 |

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/core/processor.py` | `process_content` | RobustProcessor 메인 루프 |
| `dashboard/workers/hd_render.py` | `process_content` | 편집실 재렌더링 |
