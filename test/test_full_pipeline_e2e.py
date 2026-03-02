"""8-Phase 파이프라인 전체 E2E 테스트 — post_id 1136 대상

Phase 1: analyze_resources → ResourceProfile
Phase 2: chunk_with_llm → raw script dict (Ollama 실제 호출)
Phase 3: validate_and_fix → validated script dict
Phase 4: SceneDirector → list[SceneDecision]
Phase 4.5: assign_video_modes → video_mode (t2v/i2v)
Phase 5: TTS 생성 → scene.text_lines dict 교체
Phase 6: video_prompt 생성 → scene.video_prompt
Phase 7: video_clip 생성 → scene.video_clip_path (ComfyUI LTX-2, 1~2씬 제한)
Phase 8: FFmpeg 렌더링 → 최종 9:16 영상

Usage:
    DATABASE_URL="mysql+pymysql://wagglebot:wagglebot@localhost:3306/wagglebot" \
    OLLAMA_HOST="http://localhost:11434" \
    FISH_SPEECH_URL="http://localhost:8080" \
    COMFYUI_URL="http://localhost:8188" \
    VIDEO_GEN_ENABLED="true" \
    .venv/bin/python3 test/test_full_pipeline_e2e.py
"""
import asyncio
import copy
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

# ── 프로젝트 경로 & 환경변수 설정 ──────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DATABASE_URL", "mysql+pymysql://wagglebot:wagglebot@localhost:3306/wagglebot")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("FISH_SPEECH_URL", "http://localhost:8080")
os.environ.setdefault("COMFYUI_URL", "http://localhost:8188")
os.environ.setdefault("VIDEO_GEN_ENABLED", "true")
# media/tmp이 root 소유일 수 있으므로 /tmp 하위로 우회
os.environ.setdefault("MEDIA_DIR", "/tmp/wagglebot_media")
Path(os.environ["MEDIA_DIR"]).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "_result" / "pipeline_e2e.log", mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("e2e_test")

# 결과 저장
RESULTS: dict[str, dict] = {}
OUTPUT_DIR = PROJECT_ROOT / "_result"
OUTPUT_DIR.mkdir(exist_ok=True)
TMP_DIR = Path("/tmp/wagglebot_e2e_test")
TMP_DIR.mkdir(parents=True, exist_ok=True)

POST_ID = 1136
# 시간 절약: Phase 7에서 최대 몇 개 씬만 LTX 생성
MAX_VIDEO_SCENES = 2


def banner(phase: str, title: str) -> None:
    sep = "=" * 70
    logger.info("\n%s\n  %s: %s\n%s", sep, phase, title, sep)


def elapsed_str(ms: float) -> str:
    if ms > 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


# ═══════════════════════════════════════════════════════════════════════
# DB 로드
# ═══════════════════════════════════════════════════════════════════════
banner("INIT", "DB에서 post_id 1136 로드")

from db.session import SessionLocal
from db.models import Post, Content, Comment

with SessionLocal() as db:
    post = db.query(Post).filter(Post.id == POST_ID).first()
    if not post:
        logger.error("Post %d not found!", POST_ID)
        sys.exit(1)
    images = list(post.images or [])
    content_text = post.content or ""
    title = post.title or ""
    comments = db.query(Comment).filter(Comment.post_id == POST_ID).order_by(Comment.likes.desc()).all()
    comment_data = [{"author": c.author, "content": c.content, "likes": c.likes} for c in comments]
    content_record = db.query(Content).filter(Content.post_id == POST_ID).first()
    existing_script_json = content_record.summary_text if content_record else None

RESULTS["init"] = {
    "post_id": POST_ID,
    "title": title,
    "content_length": len(content_text),
    "content_text": content_text,
    "image_count": len(images),
    "image_urls": images,
    "comments_count": len(comment_data),
    "comments": comment_data,
    "existing_video_path": content_record.video_path if content_record else None,
}
logger.info("Post 로드 완료: title=%s, images=%d, text=%d자, comments=%d",
            title, len(images), len(content_text), len(comment_data))


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Resource Analysis
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 1", "analyze_resources → ResourceProfile")

from ai_worker.pipeline.resource_analyzer import analyze_resources

t0 = time.time()
profile = analyze_resources(post, images)
elapsed_p1 = (time.time() - t0) * 1000

RESULTS["phase1"] = {
    "description": "이미지:텍스트 비율 분석으로 렌더링 전략 결정",
    "input": {
        "content_length": len(content_text),
        "image_count": len(images),
    },
    "logic": {
        "estimated_sentences": f"text_length({len(content_text)}) // 25 = {len(content_text) // 25}",
        "ratio_formula": f"image_count({len(images)}) / estimated_sentences({profile.estimated_sentences}) = {profile.ratio:.3f}",
        "strategy_thresholds": {
            "img_heavy": "ratio >= 0.7",
            "balanced": "0.3 <= ratio < 0.7",
            "text_heavy": "ratio < 0.3",
        },
    },
    "output": asdict(profile),
    "elapsed": elapsed_str(elapsed_p1),
}
logger.info("Phase 1 결과: strategy=%s, ratio=%.3f, estimated_sentences=%d",
            profile.strategy, profile.ratio, profile.estimated_sentences)


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: LLM Chunking
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 2", "chunk_with_llm → raw script dict (Ollama)")

from ai_worker.pipeline.llm_chunker import chunk_with_llm, create_chunking_prompt
from config.settings import OLLAMA_MODEL

prompt_text = create_chunking_prompt(content_text, profile, extended=True)
logger.info("LLM 프롬프트 길이: %d자", len(prompt_text))

t0 = time.time()
try:
    llm_raw = asyncio.run(chunk_with_llm(content_text, profile, post_id=POST_ID, extended=True))
    elapsed_p2 = (time.time() - t0) * 1000
    llm_success = True
    llm_error = None
    logger.info("Phase 2 LLM 호출 성공 (%s)", elapsed_str(elapsed_p2))
except Exception as e:
    elapsed_p2 = (time.time() - t0) * 1000
    llm_success = False
    llm_error = str(e)
    logger.warning("Phase 2 LLM 호출 실패: %s — 기존 DB 스크립트 사용", e)
    # 폴백: DB에 저장된 기존 스크립트 사용
    if existing_script_json:
        llm_raw = json.loads(existing_script_json)
    else:
        logger.error("기존 스크립트도 없음 — 중단")
        sys.exit(1)

RESULTS["phase2"] = {
    "description": "Ollama LLM으로 원문을 의미 단위 청킹 → hook/body/closer 구조화",
    "input": {
        "model": OLLAMA_MODEL,
        "ollama_host": os.environ.get("OLLAMA_HOST", ""),
        "strategy": profile.strategy,
        "content_length": len(content_text),
        "prompt_text": prompt_text,
        "prompt_length": len(prompt_text),
    },
    "output": {
        "hook": llm_raw.get("hook", ""),
        "body_count": len(llm_raw.get("body", [])),
        "body": llm_raw.get("body", []),
        "closer": llm_raw.get("closer", ""),
        "title_suggestion": llm_raw.get("title_suggestion", ""),
        "tags": llm_raw.get("tags", []),
        "mood": llm_raw.get("mood", ""),
    },
    "llm_call_success": llm_success,
    "llm_error": llm_error,
    "elapsed": elapsed_str(elapsed_p2),
}
logger.info("hook=%s, body=%d항목, closer=%s, mood=%s",
            llm_raw.get("hook"), len(llm_raw.get("body", [])),
            llm_raw.get("closer"), llm_raw.get("mood"))


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: Text Validation
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 3", "validate_and_fix → max_chars 검증/수정")

from ai_worker.pipeline.text_validator import validate_and_fix
from config.settings import MAX_BODY_CHARS, MAX_HOOK_CHARS

pre_fix = copy.deepcopy(llm_raw)
t0 = time.time()
validated = validate_and_fix(llm_raw)
elapsed_p3 = (time.time() - t0) * 1000

# 변경 사항 분석
changes = []
if pre_fix.get("hook", "") != validated.get("hook", ""):
    changes.append({
        "field": "hook",
        "before": pre_fix.get("hook", ""),
        "after": validated.get("hook", ""),
        "reason": f"MAX_HOOK_CHARS={MAX_HOOK_CHARS} 초과 → 잘림",
    })
if pre_fix.get("closer", "") != validated.get("closer", ""):
    changes.append({
        "field": "closer",
        "before": pre_fix.get("closer", ""),
        "after": validated.get("closer", ""),
        "reason": f"MAX_BODY_CHARS={MAX_BODY_CHARS} 초과 → 잘림",
    })

body_changes = []
for i, (old_item, new_item) in enumerate(zip(pre_fix.get("body", []), validated.get("body", []))):
    old_lines = old_item.get("lines", []) if isinstance(old_item, dict) else [old_item]
    new_lines = new_item.get("lines", []) if isinstance(new_item, dict) else [new_item]
    if old_lines != new_lines:
        body_changes.append({
            "body_index": i,
            "before": old_lines,
            "after": new_lines,
            "reason": f"라인 {MAX_BODY_CHARS}자 초과 → smart_split_korean 분할",
        })

# 최종 위반 검사
violations = []
hook_len = len(validated.get("hook", ""))
if hook_len > MAX_HOOK_CHARS:
    violations.append(f"hook ({hook_len}자 > {MAX_HOOK_CHARS}자)")
closer_len = len(validated.get("closer", ""))
if closer_len > MAX_BODY_CHARS:
    violations.append(f"closer ({closer_len}자 > {MAX_BODY_CHARS}자)")
for i, item in enumerate(validated.get("body", [])):
    for j, line in enumerate(item.get("lines", [])):
        if len(line) > MAX_BODY_CHARS:
            violations.append(f"body[{i}].lines[{j}] ({len(line)}자 > {MAX_BODY_CHARS}자)")

RESULTS["phase3"] = {
    "description": f"LLM 출력 텍스트가 MAX_HOOK_CHARS={MAX_HOOK_CHARS}, MAX_BODY_CHARS={MAX_BODY_CHARS} 제약 준수 여부 검증/수정",
    "input": {
        "hook": pre_fix.get("hook", ""),
        "hook_length": len(pre_fix.get("hook", "")),
        "body_count": len(pre_fix.get("body", [])),
        "closer": pre_fix.get("closer", ""),
        "closer_length": len(pre_fix.get("closer", "")),
        "constraints": {"MAX_HOOK_CHARS": MAX_HOOK_CHARS, "MAX_BODY_CHARS": MAX_BODY_CHARS},
    },
    "output": {
        "hook": validated.get("hook", ""),
        "hook_length": hook_len,
        "body_count": len(validated.get("body", [])),
        "body": validated.get("body", []),
        "closer": validated.get("closer", ""),
        "closer_length": closer_len,
    },
    "changes": changes,
    "body_changes": body_changes,
    "remaining_violations": violations,
    "elapsed": elapsed_str(elapsed_p3),
}
logger.info("Phase 3: %d건 변경, %d건 body 변경, 잔여 위반=%d",
            len(changes), len(body_changes), len(violations))


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Scene Director
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 4", "SceneDirector → SceneDecision 목록")

from ai_worker.pipeline.scene_director import SceneDirector

mood = validated.get("mood", "daily")
VALID_MOODS = ["humor", "touching", "anger", "sadness", "horror", "info", "controversy", "daily", "shock"]
if mood not in VALID_MOODS:
    logger.warning("LLM mood '%s' → 'daily' 대체", mood)
    mood = "daily"

t0 = time.time()
director = SceneDirector(
    profile=profile,
    images=images,
    script=validated,
    mood=mood,
)
scenes = director.direct()
elapsed_p4 = (time.time() - t0) * 1000

scene_details = []
for i, s in enumerate(scenes):
    scene_details.append({
        "index": i,
        "type": s.type,
        "text_lines": s.text_lines,
        "image_url": s.image_url[:80] if s.image_url else None,
        "mood": s.mood,
        "tts_emotion": s.tts_emotion,
        "block_type": s.block_type,
        "author": s.author,
        "bgm_path": s.bgm_path,
        "voice_override": s.voice_override,
        "emotion_tag": s.emotion_tag,
    })

type_counts = {}
for s in scenes:
    type_counts[s.type] = type_counts.get(s.type, 0) + 1

RESULTS["phase4"] = {
    "description": "validated script + ResourceProfile + images → SceneDecision 목록 (씬 배분 + 감정 태그 + BGM 할당)",
    "input": {
        "strategy": profile.strategy,
        "image_count": len(images),
        "body_items": len(validated.get("body", [])),
        "mood": mood,
        "has_hook": bool(validated.get("hook")),
        "has_closer": bool(validated.get("closer")),
        "comment_count": len(comment_data),
    },
    "output": {
        "total_scenes": len(scenes),
        "type_distribution": type_counts,
        "scenes": scene_details,
    },
    "logic": {
        "scene_policy": "config/scene_policy.json에서 mood별 프리셋 로드",
        "intro": "hook → intro 씬, BGM 할당",
        "body_mapping": "strategy에 따라 img_text / text_only / img_only 분배",
        "comment_scenes": "상위 댓글 → text_only(block_type=comment) 삽입",
        "outro": "closer → outro 씬",
    },
    "elapsed": elapsed_str(elapsed_p4),
}
logger.info("Phase 4: %d씬 — %s", len(scenes), type_counts)


# ═══════════════════════════════════════════════════════════════════════
# Phase 4.5: Video Mode Assignment
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 4.5", "assign_video_modes → t2v/i2v")

from ai_worker.pipeline.scene_director import assign_video_modes

image_cache_dir = TMP_DIR / "image_cache"
image_cache_dir.mkdir(parents=True, exist_ok=True)

t0 = time.time()
try:
    scenes = assign_video_modes(scenes, image_cache_dir, i2v_threshold=0.6)
    elapsed_p45 = (time.time() - t0) * 1000
    vm_success = True
    vm_error = None
except Exception as e:
    elapsed_p45 = (time.time() - t0) * 1000
    vm_success = False
    vm_error = str(e)
    logger.warning("Phase 4.5 실패: %s", e)

video_mode_details = []
for i, s in enumerate(scenes):
    video_mode_details.append({
        "index": i,
        "type": s.type,
        "video_mode": s.video_mode,
        "video_init_image": s.video_init_image,
        "has_image": bool(s.image_url),
    })

mode_counts = {}
for s in scenes:
    m = s.video_mode or "none"
    mode_counts[m] = mode_counts.get(m, 0) + 1

RESULTS["phase4_5"] = {
    "description": "각 씬의 비디오 생성 모드 결정 — 이미지 있으면 I2V 적합성 평가, 없으면 T2V",
    "input": {
        "total_scenes": len(scenes),
        "i2v_threshold": 0.6,
        "image_cache_dir": str(image_cache_dir),
    },
    "output": {
        "mode_distribution": mode_counts,
        "scenes": video_mode_details,
    },
    "logic": {
        "t2v": "이미지 없는 씬 → Text-to-Video",
        "i2v": "이미지 적합성 점수 >= 0.6 → Image-to-Video",
        "t2v_fallback": "이미지 있지만 적합성 < 0.6 → T2V 전환",
        "none": "intro/outro 등 비디오 생성 제외 씬",
    },
    "success": vm_success,
    "error": vm_error,
    "elapsed": elapsed_str(elapsed_p45),
}
logger.info("Phase 4.5: %s (success=%s)", mode_counts, vm_success)


# ═══════════════════════════════════════════════════════════════════════
# Phase 5: TTS 합성
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 5", "TTS 합성 (Fish Speech)")

tts_results = []
tts_success_count = 0

try:
    from ai_worker.tts.fish_client import synthesize as fish_synthesize

    for i, scene in enumerate(scenes):
        if not scene.text_lines:
            continue
        # text_lines에서 텍스트 추출
        raw_line = scene.text_lines[0]
        text = raw_line.get("text", "") if isinstance(raw_line, dict) else str(raw_line)
        if not text:
            continue

        logger.info("TTS [씬%d/%s]: '%s'", i, scene.type, text[:50])
        t0 = time.time()
        try:
            audio_path = asyncio.run(fish_synthesize(
                text,
                scene_type=scene.type,
                emotion=scene.tts_emotion or "",
            ))
            elapsed_tts = (time.time() - t0) * 1000
            file_size = audio_path.stat().st_size if audio_path and audio_path.exists() else 0
            tts_results.append({
                "scene_index": i,
                "scene_type": scene.type,
                "input_text": text,
                "emotion": scene.tts_emotion,
                "audio_path": str(audio_path),
                "file_size_bytes": file_size,
                "elapsed": elapsed_str(elapsed_tts),
                "success": True,
            })
            # text_lines를 dict 형식으로 교체 (Phase 5 사양)
            scene.text_lines[0] = {"text": text, "audio": str(audio_path)}
            tts_success_count += 1
            logger.info("TTS 성공: %s (%d bytes, %s)", audio_path.name, file_size, elapsed_str(elapsed_tts))
        except Exception as e:
            elapsed_tts = (time.time() - t0) * 1000
            tts_results.append({
                "scene_index": i,
                "scene_type": scene.type,
                "input_text": text,
                "error": str(e),
                "elapsed": elapsed_str(elapsed_tts),
                "success": False,
            })
            logger.warning("TTS 실패 [씬%d]: %s", i, e)

except ImportError as e:
    tts_results.append({"error": f"TTS 모듈 임포트 실패: {e}", "success": False})
except Exception as e:
    tts_results.append({"error": f"TTS 초기화 실패: {e}", "success": False})

RESULTS["phase5"] = {
    "description": "Fish Speech TTS로 각 씬의 텍스트 → 음성 합성. scene.text_lines가 str→dict{'text','audio'}로 교체됨",
    "input": {
        "tts_engine": "fish-speech",
        "fish_speech_url": os.environ.get("FISH_SPEECH_URL", ""),
        "total_scenes": len(scenes),
        "scenes_with_text": sum(1 for s in scenes if s.text_lines),
    },
    "output": {
        "success_count": tts_success_count,
        "fail_count": sum(1 for r in tts_results if not r.get("success")),
        "details": tts_results,
    },
    "logic": {
        "text_normalization": "슬랭 제거, 숫자→한국어 변환",
        "emotion_mapping": "scene.tts_emotion → Fish Speech reference audio 선택",
        "output_format": "WAV → ffmpeg 후처리",
    },
}
logger.info("Phase 5: %d/%d 성공", tts_success_count, len(tts_results))


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Video Prompt 생성
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 6", "VideoPromptEngine → video_prompt (한→영)")

prompt_results = []
p6_success_count = 0

try:
    from ai_worker.video.prompt_engine import VideoPromptEngine

    body_texts = [" ".join(item.get("lines", [])) for item in validated.get("body", [])[:5]]
    body_summary = " / ".join(body_texts)

    engine = VideoPromptEngine()

    # video_mode가 설정된 씬만 대상 (Phase 4.5에서 t2v/i2v 할당된 것)
    prompt_scenes = [s for s in scenes if s.video_mode in ("t2v", "i2v")]
    logger.info("비디오 프롬프트 생성 대상: %d씬", len(prompt_scenes))

    t0 = time.time()
    try:
        result_scenes = engine.generate_batch(
            scenes=prompt_scenes,
            mood=mood,
            title=title,
            body_summary=body_summary,
        )
        elapsed_p6 = (time.time() - t0) * 1000

        for i, s in enumerate(result_scenes):
            prompt_results.append({
                "scene_index": scenes.index(s) if s in scenes else i,
                "type": s.type,
                "video_mode": s.video_mode,
                "input_text": s.text_lines[0] if s.text_lines else "",
                "video_prompt": s.video_prompt,
                "video_prompt_length": len(s.video_prompt or ""),
            })
            if s.video_prompt:
                p6_success_count += 1
            logger.info("Prompt [%d]: %s", i, (s.video_prompt or "")[:150])
    except Exception as e:
        elapsed_p6 = (time.time() - t0) * 1000
        prompt_results.append({"error": str(e)})
        logger.warning("Phase 6 generate_batch 실패: %s", e)

except ImportError as e:
    elapsed_p6 = 0
    prompt_results.append({"error": f"VideoPromptEngine 임포트 실패: {e}"})

RESULTS["phase6"] = {
    "description": "Ollama LLM으로 한국어 씬 텍스트 → LTX-2용 영어 비디오 프롬프트 변환",
    "input": {
        "mood": mood,
        "title": title,
        "body_summary": body_summary[:200] if "body_summary" in dir() else "",
        "target_scene_count": len(prompt_scenes) if "prompt_scenes" in dir() else 0,
        "model": OLLAMA_MODEL,
    },
    "output": {
        "success_count": p6_success_count,
        "prompts": prompt_results,
    },
    "logic": {
        "t2v_prompt": "한국어 텍스트 + mood + body_summary → 영어 시각 프롬프트 (max_tokens=180)",
        "i2v_prompt": "초기 이미지 설명 + 한국어 텍스트 → 영어 모션 프롬프트 (max_tokens=120)",
        "simplified": "실패 시 단순화된 프롬프트 (max_tokens=80)",
        "video_styles": "config/video_styles.json에서 mood별 비주얼 키워드 참조",
    },
    "elapsed": elapsed_str(elapsed_p6) if "elapsed_p6" in dir() else "N/A",
}


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Video Clip 생성 (ComfyUI LTX-2) — 1~2씬 제한
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 7", f"ComfyUI LTX-2 비디오 생성 (최대 {MAX_VIDEO_SCENES}씬)")

video_gen_results = []
p7_success_count = 0

try:
    from ai_worker.video.comfy_client import ComfyUIClient
    from ai_worker.video.manager import VideoManager
    from config.settings import get_comfyui_url

    comfy_url = get_comfyui_url()
    comfy = ComfyUIClient(base_url=comfy_url)

    # ComfyUI 헬스체크
    import requests as _req
    comfy_health = _req.get(f"{comfy_url}/system_stats", timeout=5)
    comfy_healthy = comfy_health.ok
    logger.info("ComfyUI 상태: %s", "healthy" if comfy_healthy else f"unhealthy ({comfy_health.status_code})")

    if comfy_healthy:
        # video_prompt가 있는 씬만 대상, MAX_VIDEO_SCENES 제한
        gen_candidates = [s for s in scenes if s.video_prompt]
        gen_targets = gen_candidates[:MAX_VIDEO_SCENES]
        logger.info("비디오 생성 대상: %d씬 (전체 %d 중 %d 제한)",
                    len(gen_targets), len(gen_candidates), MAX_VIDEO_SCENES)

        video_config = {
            "VIDEO_RESOLUTION": (1280, 720),
            "VIDEO_RESOLUTION_FALLBACK": (768, 512),
            "VIDEO_NUM_FRAMES": 97,
            "VIDEO_NUM_FRAMES_FALLBACK": 65,
            "VIDEO_GEN_TIMEOUT": 1200,
            "VIDEO_GEN_TIMEOUT_DISTILLED": 600,
            "VIDEO_MAX_CLIPS_PER_POST": MAX_VIDEO_SCENES,
            "VIDEO_MAX_RETRY": 4,
            "VIDEO_STEPS": 20,
            "VIDEO_CFG": 3.5,
            "VIDEO_STEPS_DISTILLED": 8,
            "VIDEO_CFG_DISTILLED": 1.0,
            "VIDEO_FPS": 24,
        }

        # prompt가 없는 씬은 제거하여 generate_all_clips에 넘길 목록 구성
        engine_for_mgr = VideoPromptEngine()
        manager = VideoManager(
            comfy_client=comfy,
            prompt_engine=engine_for_mgr,
            config=video_config,
        )

        body_texts_p7 = [" ".join(item.get("lines", [])) for item in validated.get("body", [])[:5]]
        body_summary_p7 = " / ".join(body_texts_p7)

        t0 = time.time()
        try:
            result_scenes = asyncio.run(manager.generate_all_clips(
                scenes=gen_targets,
                mood=mood,
                post_id=POST_ID,
                title=title,
                body_summary=body_summary_p7,
            ))
            elapsed_p7 = (time.time() - t0) * 1000

            for i, s in enumerate(result_scenes):
                clip_exists = bool(s.video_clip_path) and Path(s.video_clip_path).exists()
                clip_size = Path(s.video_clip_path).stat().st_size if clip_exists else 0
                video_gen_results.append({
                    "scene_index": i,
                    "type": s.type,
                    "video_mode": s.video_mode,
                    "video_prompt": (s.video_prompt or "")[:150],
                    "video_clip_path": s.video_clip_path,
                    "clip_exists": clip_exists,
                    "clip_size_bytes": clip_size,
                    "video_generation_failed": s.video_generation_failed,
                })
                if clip_exists:
                    p7_success_count += 1
                    # 결과를 원본 scenes 목록에도 반영
                    for orig_s in scenes:
                        if orig_s.text_lines == s.text_lines:
                            orig_s.video_clip_path = s.video_clip_path
                            break
                logger.info("비디오 [%d]: clip=%s, exists=%s, size=%d",
                            i, s.video_clip_path, clip_exists, clip_size)
        except Exception as e:
            elapsed_p7 = (time.time() - t0) * 1000
            video_gen_results.append({"error": str(e)})
            logger.error("Phase 7 generate_all_clips 실패: %s", e, exc_info=True)
    else:
        elapsed_p7 = 0
        video_gen_results.append({"error": "ComfyUI unhealthy"})

except ImportError as e:
    elapsed_p7 = 0
    video_gen_results.append({"error": f"비디오 모듈 임포트 실패: {e}"})
except Exception as e:
    elapsed_p7 = 0
    video_gen_results.append({"error": f"Phase 7 초기화 실패: {e}"})
    logger.error("Phase 7 예외: %s", e, exc_info=True)

RESULTS["phase7"] = {
    "description": f"ComfyUI + LTX-2 19B FP8로 씬별 비디오 클립 생성 (최대 {MAX_VIDEO_SCENES}씬 제한)",
    "input": {
        "comfyui_url": comfy_url if "comfy_url" in dir() else "N/A",
        "comfyui_healthy": comfy_healthy if "comfy_healthy" in dir() else False,
        "target_scenes": len(gen_targets) if "gen_targets" in dir() else 0,
        "config": video_config if "video_config" in dir() else {},
    },
    "output": {
        "success_count": p7_success_count,
        "results": video_gen_results,
    },
    "logic": {
        "fallback_strategy": [
            "1차: Full(1280x720, 97f, 20step) + 원본 프롬프트",
            "2차: Full + 단순화 프롬프트",
            "3차: 저해상도(768x512, 65f, 15step) + 단순화",
            "4차: Distilled(8step, CFG=1.0) + 단순화",
        ],
        "frame_rule": "LTX-2 프레임 수 = 1+8k (9, 17, 25, ..., 97)",
        "on_all_fail": "씬 삭제 + text_lines 인접 씬 병합",
    },
    "elapsed": elapsed_str(elapsed_p7) if "elapsed_p7" in dir() else "N/A",
}


# ═══════════════════════════════════════════════════════════════════════
# Phase 8: FFmpeg 렌더링
# ═══════════════════════════════════════════════════════════════════════
banner("Phase 8", "FFmpeg 렌더링 → 최종 9:16 영상")

render_result = {}
try:
    from ai_worker.renderer.layout import render_layout_video_from_scenes

    output_video = TMP_DIR / f"post_{POST_ID}_e2e_test.mp4"

    # 씬 상태 로그
    video_scene_count = sum(1 for s in scenes if s.video_clip_path and not s.video_generation_failed)
    static_scene_count = len(scenes) - video_scene_count
    logger.info("렌더링 입력: %d씬 (비디오=%d, 정적=%d)", len(scenes), video_scene_count, static_scene_count)

    t0 = time.time()
    result_path = render_layout_video_from_scenes(
        post=post,
        scenes=scenes,
        output_path=output_video,
    )
    elapsed_p8 = (time.time() - t0) * 1000

    if result_path and result_path.exists():
        file_size = result_path.stat().st_size
        # ffprobe로 비디오 정보 추출
        try:
            probe_cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(result_path),
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
            probe_data = json.loads(probe_result.stdout) if probe_result.returncode == 0 else {}
        except Exception as pe:
            probe_data = {"error": str(pe)}

        # _result/에 복사
        result_copy = OUTPUT_DIR / f"post_{POST_ID}_e2e.mp4"
        import shutil
        shutil.copy2(result_path, result_copy)
        logger.info("렌더링 결과 복사: %s", result_copy)

        render_result = {
            "output_path": str(result_path),
            "result_copy": str(result_copy),
            "file_size_mb": round(file_size / 1024 / 1024, 2),
            "ffprobe": probe_data,
            "success": True,
        }
        logger.info("Phase 8 성공: %s (%.2f MB, %s)",
                    result_path.name, file_size / 1024 / 1024, elapsed_str(elapsed_p8))
    else:
        render_result = {"success": False, "error": "렌더링 결과 파일 없음"}

except Exception as e:
    elapsed_p8 = (time.time() - t0) * 1000 if "t0" in dir() else 0
    render_result = {"success": False, "error": str(e)}
    logger.error("Phase 8 실패: %s", e, exc_info=True)

# scene_idx 매핑 검증
from ai_worker.renderer.layout import _scenes_to_plan_and_sentences, _get_scene_for_entry
plan_sentences, plan_entries, plan_images = _scenes_to_plan_and_sentences(scenes)
scene_idx_check = []
for entry in plan_entries:
    scene = _get_scene_for_entry(entry, plan_sentences, scenes)
    scene_idx_check.append({
        "entry_type": entry.get("type"),
        "scene_idx": entry.get("scene_idx"),
        "matched": scene is not None,
        "has_video_clip": bool(getattr(scene, "video_clip_path", None)) if scene else False,
    })

RESULTS["phase8"] = {
    "description": "모든 씬을 FFmpeg(h264_nvenc)로 최종 9:16 합성. 비디오 씬은 하이브리드 렌더링, 정적 씬은 이미지+텍스트 합성",
    "input": {
        "total_scenes": len(scenes),
        "video_scenes": video_scene_count if "video_scene_count" in dir() else 0,
        "static_scenes": static_scene_count if "static_scene_count" in dir() else 0,
        "tts_success": tts_success_count,
    },
    "output": render_result,
    "scene_idx_mapping": {
        "total_plan_entries": len(plan_entries),
        "all_have_scene_idx": all(e.get("scene_idx") is not None for e in plan_entries),
        "all_matched": all(c["matched"] for c in scene_idx_check),
        "video_entries": sum(1 for c in scene_idx_check if c["has_video_clip"]),
        "details": scene_idx_check,
    },
    "logic": {
        "hybrid_rendering": "video_clip_path가 있는 씬 → 비디오 세그먼트 + 자막 오버레이",
        "static_rendering": "video_clip_path가 없는 씬 → 이미지 + 텍스트 레이아웃 합성",
        "codec": "h264_nvenc (RTX 3090)",
        "scene_idx_fix": "plan entry에 scene_idx 추가하여 O(1) 조회 (텍스트 매칭 폴백 대신)",
    },
    "elapsed": elapsed_str(elapsed_p8) if "elapsed_p8" in dir() else "N/A",
}


# ═══════════════════════════════════════════════════════════════════════
# 서비스 상태 스냅샷
# ═══════════════════════════════════════════════════════════════════════
import requests as _req

service_status = {}
try:
    r = _req.get("http://localhost:11434/api/tags", timeout=5)
    service_status["ollama"] = {
        "status": "healthy",
        "models": [m.get("name") for m in r.json().get("models", [])] if r.ok else [],
    }
except Exception as e:
    service_status["ollama"] = {"status": f"unreachable: {e}"}

try:
    r = _req.get("http://localhost:8188/system_stats", timeout=5)
    if r.ok:
        devs = r.json().get("devices", [])
        service_status["comfyui"] = {
            "status": "healthy",
            "devices": [{
                "name": d["name"],
                "vram_total_mb": d["vram_total"] // 1024 // 1024,
                "vram_free_mb": d["vram_free"] // 1024 // 1024,
            } for d in devs],
        }
    else:
        service_status["comfyui"] = {"status": f"unhealthy ({r.status_code})"}
except Exception as e:
    service_status["comfyui"] = {"status": f"unreachable: {e}"}

try:
    r = _req.get("http://localhost:8080/v1/health", timeout=5)
    service_status["fish_speech"] = {"status": "healthy" if r.ok else f"unhealthy ({r.status_code})"}
except Exception as e:
    service_status["fish_speech"] = {"status": f"unreachable: {e}"}

RESULTS["service_status"] = service_status


# ═══════════════════════════════════════════════════════════════════════
# 결과 저장
# ═══════════════════════════════════════════════════════════════════════
banner("SAVE", "결과 저장")

result_json_path = OUTPUT_DIR / "pipeline_e2e_results.json"
with open(result_json_path, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2, default=str)
logger.info("JSON 결과: %s", result_json_path)

# ── 요약 출력 ──
sep = "=" * 70
print(f"\n{sep}")
print(f"  8-Phase E2E 파이프라인 테스트 완료  (post_id={POST_ID})")
print(sep)
print(f"  Phase 1  리소스 분석:  strategy={profile.strategy}, ratio={profile.ratio:.3f}")
print(f"  Phase 2  LLM 청킹:    hook='{llm_raw.get('hook', '')}', body={len(llm_raw.get('body', []))}개, success={llm_success}")
print(f"  Phase 3  텍스트 검증:  위반={len(violations)}건, 변경={len(changes)+len(body_changes)}건")
print(f"  Phase 4  씬 배분:      {len(scenes)}씬 — {type_counts}")
print(f"  Phase 4.5 비디오 모드: {mode_counts}")
print(f"  Phase 5  TTS:          {tts_success_count}건 성공")
print(f"  Phase 6  프롬프트:     {p6_success_count}건 생성")
print(f"  Phase 7  비디오 클립:  {p7_success_count}건 생성")
p8_ok = render_result.get("success", False)
p8_size = render_result.get("file_size_mb", 0)
print(f"  Phase 8  렌더링:       {'성공' if p8_ok else '실패'} ({p8_size} MB)")
print(f"\n  결과 JSON: {result_json_path}")
print(f"  로그 파일: {OUTPUT_DIR / 'pipeline_e2e.log'}")
if p8_ok:
    print(f"  렌더링 영상: {render_result.get('result_copy', 'N/A')}")
print(sep)
