"""8-Phase 파이프라인 통합 테스트 — 경량화 버전

Phase 1~6 + Phase 7-8 상태 점검.
기본값: post_id=651, 이미지 3장, 텍스트 200자, 댓글 2개.

Usage:
    DATABASE_URL="mysql+pymysql://wagglebot:wagglebot@localhost/wagglebot" \
    TEST_POST_ID=651 \
    .venv/bin/python3 test/test_pipeline_phases.py
"""
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://wagglebot:wagglebot@localhost/wagglebot")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
for _noisy in ("websockets", "websockets.client", "urllib3", "httpx", "httpcore", "PIL"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("pipeline_test")

# 결과 저장
RESULTS: dict[str, dict] = {}
OUTPUT_DIR = PROJECT_ROOT / "_result"
OUTPUT_DIR.mkdir(exist_ok=True)
TMP_DIR = Path("/tmp/wagglebot_pipeline_test")
TMP_DIR.mkdir(parents=True, exist_ok=True)


def banner(phase: str, title: str) -> None:
    sep = "=" * 70
    logger.info("\n%s\n  %s: %s\n%s", sep, phase, title, sep)


# ─────────────────────────────────────────────────────────────────────
# DB에서 post 로드
# ─────────────────────────────────────────────────────────────────────
from db.session import SessionLocal
from db.models import Post, Content, Comment

POST_ID = int(os.environ.get("TEST_POST_ID", 651))

# ── 경량화 파라미터 ──
MAX_TEST_IMAGES = int(os.environ.get("MAX_TEST_IMAGES", 3))
MAX_TEST_TEXT_CHARS = int(os.environ.get("MAX_TEST_TEXT_CHARS", 200))
MAX_TEST_COMMENTS = int(os.environ.get("MAX_TEST_COMMENTS", 2))

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

# ── 데이터 경량화 (테스트 시간 단축) ──
orig_images, orig_text_len, orig_comments = len(images), len(content_text), len(comment_data)
images = images[:MAX_TEST_IMAGES]
if len(content_text) > MAX_TEST_TEXT_CHARS:
    import re
    sentences = re.split(r'(?<=[.!?。])\s*', content_text)
    truncated = ""
    for sent in sentences:
        if len(truncated) + len(sent) > MAX_TEST_TEXT_CHARS:
            break
        truncated += sent + " "
    content_text = truncated.strip() or content_text[:MAX_TEST_TEXT_CHARS]
comment_data = comment_data[:MAX_TEST_COMMENTS]

# post 객체에 경량화 반영
post.content = content_text
post.images = images

logger.info("경량화: images %d→%d, text %d→%d자, comments %d→%d",
            orig_images, len(images), orig_text_len, len(content_text),
            orig_comments, len(comment_data))
logger.info("Post loaded: id=%d, title=%s, images=%d, text=%d chars, comments=%d",
            POST_ID, title, len(images), len(content_text), len(comment_data))

# ─────────────────────────────────────────────────────────────────────
# Phase 1: Resource Analysis
# ─────────────────────────────────────────────────────────────────────
banner("Phase 1", "analyze_resources → ResourceProfile")

from ai_worker.scene.analyzer import analyze_resources

t0 = time.time()
profile = analyze_resources(post, images)
elapsed_p1 = (time.time() - t0) * 1000

RESULTS["phase1"] = {
    "input": {
        "post_id": POST_ID,
        "title": title,
        "content_length": len(content_text),
        "image_count": len(images),
        "image_urls": images,
        "comments_count": len(comment_data),
    },
    "output": asdict(profile),
    "elapsed_ms": round(elapsed_p1, 1),
    "analysis": {
        "chars_per_sentence_used": 25,
        "formula": "image_count / estimated_sentences",
        "strategy_thresholds": {"image_heavy": "ratio >= 0.7", "balanced": "0.3 <= ratio < 0.7", "text_heavy": "ratio < 0.3"},
    },
}
logger.info("Phase 1 결과: %s", json.dumps(RESULTS["phase1"]["output"], ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────
# Phase 2: LLM Chunking (실제 Ollama 호출)
# ─────────────────────────────────────────────────────────────────────
banner("Phase 2", "chunk_with_llm → raw script dict")

from ai_worker.script.chunker import chunk_with_llm, create_chunking_prompt
from config.settings import OLLAMA_MODEL

# 프롬프트 미리보기
prompt_text = create_chunking_prompt(content_text, profile, extended=True)
logger.info("LLM 프롬프트 길이: %d자", len(prompt_text))
logger.info("LLM 프롬프트 (첫 500자):\n%s", prompt_text[:500])

# 기존 DB 결과 로드 (비교용)
existing_script = None
if existing_script_json:
    try:
        existing_script = json.loads(existing_script_json)
        logger.info("기존 DB 스크립트 로드 성공 (hook=%s)", existing_script.get("hook", "N/A"))
    except json.JSONDecodeError:
        logger.warning("기존 DB 스크립트가 JSON이 아닙니다 (레거시 텍스트)")

# 실제 LLM 호출
try:
    t0 = time.time()
    llm_raw = asyncio.run(chunk_with_llm(content_text, profile, post_id=POST_ID, extended=True))
    elapsed_p2 = (time.time() - t0) * 1000
    llm_success = True
    llm_error = None
    logger.info("Phase 2 LLM 호출 성공 (%dms)", elapsed_p2)
except Exception as e:
    llm_raw = existing_script or {}
    elapsed_p2 = 0
    llm_success = False
    llm_error = str(e)
    logger.warning("Phase 2 LLM 호출 실패: %s — 기존 DB 스크립트 사용", e)

RESULTS["phase2"] = {
    "input": {
        "model": OLLAMA_MODEL,
        "strategy": profile.strategy,
        "content_length": len(content_text),
        "extended": True,
        "prompt_length": len(prompt_text),
        "prompt_preview": prompt_text[:800],
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
    "elapsed_ms": round(elapsed_p2, 1),
    "llm_call_success": llm_success,
    "llm_error": llm_error,
    "existing_db_script": existing_script,
}
logger.info("Phase 2 결과 hook: %s", llm_raw.get("hook", ""))
logger.info("Phase 2 결과 body(%d): %s", len(llm_raw.get("body", [])), json.dumps(llm_raw.get("body", []), ensure_ascii=False)[:500])
logger.info("Phase 2 결과 closer: %s", llm_raw.get("closer", ""))
logger.info("Phase 2 mood: %s, tags: %s", llm_raw.get("mood", ""), llm_raw.get("tags", []))


# ─────────────────────────────────────────────────────────────────────
# Phase 3: Text Validation
# ─────────────────────────────────────────────────────────────────────
banner("Phase 3", "validate_and_fix → validated script dict")

from ai_worker.scene.validator import validate_and_fix
from config.settings import MAX_BODY_CHARS, MAX_HOOK_CHARS

import copy
pre_fix = copy.deepcopy(llm_raw)

t0 = time.time()
validated = validate_and_fix(llm_raw)
elapsed_p3 = (time.time() - t0) * 1000

# 변경 사항 분석
changes = []
if pre_fix.get("hook", "") != validated.get("hook", ""):
    changes.append(f"hook 변경: '{pre_fix.get('hook', '')}' → '{validated.get('hook', '')}'")
if pre_fix.get("closer", "") != validated.get("closer", ""):
    changes.append(f"closer 변경: '{pre_fix.get('closer', '')}' → '{validated.get('closer', '')}'")

body_changes = []
for i, (old_item, new_item) in enumerate(zip(pre_fix.get("body", []), validated.get("body", []))):
    old_lines = old_item.get("lines", []) if isinstance(old_item, dict) else [old_item]
    new_lines = new_item.get("lines", []) if isinstance(new_item, dict) else [new_item]
    if old_lines != new_lines:
        body_changes.append({"index": i, "before": old_lines, "after": new_lines})

# 글자 수 초과 검사
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
            violations.append(f"body[{i}].lines[{j}] ({len(line)}자 > {MAX_BODY_CHARS}자): '{line}'")

RESULTS["phase3"] = {
    "input": {
        "hook_length": len(pre_fix.get("hook", "")),
        "body_count": len(pre_fix.get("body", [])),
        "closer_length": len(pre_fix.get("closer", "")),
        "constraints": {"MAX_HOOK_CHARS": MAX_HOOK_CHARS, "MAX_BODY_CHARS": MAX_BODY_CHARS},
    },
    "output": {
        "hook": validated.get("hook", ""),
        "hook_length": hook_len,
        "body_count": len(validated.get("body", [])),
        "closer": validated.get("closer", ""),
        "closer_length": closer_len,
        "validated_body": validated.get("body", []),
    },
    "changes": changes,
    "body_changes": body_changes,
    "remaining_violations": violations,
    "elapsed_ms": round(elapsed_p3, 1),
}
logger.info("Phase 3 변경사항: %d건", len(changes) + len(body_changes))
if violations:
    logger.warning("Phase 3 잔여 위반: %s", violations)
else:
    logger.info("Phase 3 위반 없음 — 모든 글자 수 정상")


# ─────────────────────────────────────────────────────────────────────
# Phase 4: Scene Director
# ─────────────────────────────────────────────────────────────────────
banner("Phase 4", "SceneDirector.direct() → list[SceneDecision]")

from ai_worker.scene.director import SceneDirector

mood = validated.get("mood", "daily")
# mood 보정 (LLM이 잘못된 mood를 줄 수 있음)
VALID_MOODS = ["humor", "touching", "anger", "sadness", "horror", "info", "controversy", "daily", "shock"]
if mood not in VALID_MOODS:
    logger.warning("LLM mood '%s' 미인식, 'daily'로 대체", mood)
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

scene_summary = []
for i, s in enumerate(scenes):
    scene_info = {
        "index": i,
        "type": s.type,
        "text_lines": s.text_lines,
        "image_url": s.image_url[:80] if s.image_url else None,
        "mood": s.mood,
        "tts_emotion": s.tts_emotion,
        "block_type": s.block_type,
        "author": s.author,
        "bgm_path": s.bgm_path,
    }
    scene_summary.append(scene_info)

type_counts = {}
for s in scenes:
    type_counts[s.type] = type_counts.get(s.type, 0) + 1

RESULTS["phase4"] = {
    "input": {
        "strategy": profile.strategy,
        "image_count": len(images),
        "body_items": len(validated.get("body", [])),
        "mood": mood,
    },
    "output": {
        "total_scenes": len(scenes),
        "type_distribution": type_counts,
        "scenes": scene_summary,
    },
    "elapsed_ms": round(elapsed_p4, 1),
}
logger.info("Phase 4 결과: %d씬 — %s", len(scenes), type_counts)


# ─────────────────────────────────────────────────────────────────────
# Phase 4.5: Video Mode Assignment
# ─────────────────────────────────────────────────────────────────────
banner("Phase 4.5", "assign_video_modes → video_mode (t2v/i2v)")

from ai_worker.scene.director import assign_video_modes

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

video_mode_summary = []
for i, s in enumerate(scenes):
    video_mode_summary.append({
        "index": i,
        "type": s.type,
        "video_mode": s.video_mode,
        "video_init_image": s.video_init_image,
        "image_url_short": s.image_url[:60] if s.image_url else None,
    })

mode_counts = {}
for s in scenes:
    m = s.video_mode or "none"
    mode_counts[m] = mode_counts.get(m, 0) + 1

RESULTS["phase4_5"] = {
    "input": {
        "total_scenes": len(scenes),
        "i2v_threshold": 0.6,
        "image_cache_dir": str(image_cache_dir),
    },
    "output": {
        "mode_distribution": mode_counts,
        "scenes": video_mode_summary,
    },
    "success": vm_success,
    "error": vm_error,
    "elapsed_ms": round(elapsed_p45, 1),
}
logger.info("Phase 4.5 결과: %s", mode_counts)


# ─────────────────────────────────────────────────────────────────────
# Phase 5: TTS 합성 (샘플 1~2개)
# ─────────────────────────────────────────────────────────────────────
banner("Phase 5", "TTS 합성 (Fish Speech)")

tts_results = []
try:
    from ai_worker.tts.fish_client import synthesize as fish_synthesize

    # 첫 2개 body 씬에 대해서만 TTS 테스트
    test_scenes = [s for s in scenes if s.type in ("image_text", "text_only")][:2]
    for i, scene in enumerate(test_scenes):
        text = scene.text_lines[0] if scene.text_lines else ""
        if not text:
            continue
        logger.info("TTS 합성 시도 [%d]: '%s'", i, text[:40])
        t0 = time.time()
        try:
            audio_path = asyncio.run(fish_synthesize(text, scene.type))
            elapsed_tts = (time.time() - t0) * 1000
            file_size = audio_path.stat().st_size if audio_path and audio_path.exists() else 0
            tts_results.append({
                "scene_index": i,
                "text": text,
                "audio_path": str(audio_path),
                "file_size_bytes": file_size,
                "elapsed_ms": round(elapsed_tts, 1),
                "success": True,
            })
            logger.info("TTS 성공: %s (%d bytes, %dms)", audio_path, file_size, elapsed_tts)
        except Exception as e:
            elapsed_tts = (time.time() - t0) * 1000
            tts_results.append({
                "scene_index": i,
                "text": text,
                "error": str(e),
                "elapsed_ms": round(elapsed_tts, 1),
                "success": False,
            })
            logger.warning("TTS 실패 [%d]: %s", i, e)

except ImportError as e:
    tts_results.append({"error": f"TTS 모듈 임포트 실패: {e}", "success": False})
except Exception as e:
    tts_results.append({"error": f"TTS 초기화 실패: {e}", "success": False})

RESULTS["phase5"] = {
    "input": {
        "tts_engine": "fish-speech",
        "test_count": len([r for r in tts_results if "text" in r]),
    },
    "output": tts_results,
}


# ─────────────────────────────────────────────────────────────────────
# Phase 6: Video Prompt 생성
# ─────────────────────────────────────────────────────────────────────
banner("Phase 6", "VideoPromptEngine → video_prompt (한→영)")

prompt_results = []
try:
    from ai_worker.video.prompt_engine import VideoPromptEngine

    body_texts = [" ".join(item.get("lines", [])) for item in validated.get("body", [])[:3]]
    body_summary = " / ".join(body_texts)

    engine = VideoPromptEngine()
    # 테스트용 씬 2개
    test_prompt_scenes = [s for s in scenes if s.type in ("image_text", "text_only")][:2]

    t0 = time.time()
    try:
        result_scenes = engine.generate_batch(
            scenes=test_prompt_scenes,
            mood=mood,
            title=title,
            body_summary=body_summary,
        )
        elapsed_p6 = (time.time() - t0) * 1000

        for i, s in enumerate(result_scenes):
            prompt_results.append({
                "scene_index": i,
                "type": s.type,
                "video_mode": s.video_mode,
                "video_prompt": s.video_prompt,
                "text_lines": s.text_lines,
            })
            logger.info("Video prompt [%d]: %s", i, (s.video_prompt or "")[:200])
    except Exception as e:
        elapsed_p6 = (time.time() - t0) * 1000
        prompt_results.append({"error": str(e), "elapsed_ms": round(elapsed_p6, 1)})
        logger.warning("Phase 6 실패: %s", e)

except ImportError as e:
    elapsed_p6 = 0
    prompt_results.append({"error": f"VideoPromptEngine 임포트 실패: {e}"})

RESULTS["phase6"] = {
    "input": {
        "mood": mood,
        "title": title,
        "body_summary_preview": body_summary[:200] if "body_summary" in dir() else "",
        "test_scene_count": len(test_prompt_scenes) if "test_prompt_scenes" in dir() else 0,
    },
    "output": prompt_results,
    "elapsed_ms": round(elapsed_p6, 1) if "elapsed_p6" in dir() else 0,
}


# ─────────────────────────────────────────────────────────────────────
# Phase 7-8: 기존 결과물 확인
# ─────────────────────────────────────────────────────────────────────
banner("Phase 7-8", "기존 비디오/렌더링 결과물 확인")

existing_results = {}
if content_record:
    video_path = content_record.video_path
    audio_path_str = content_record.audio_path

    # 비디오 파일 확인
    if video_path:
        # docker 경로와 호스트 경로 모두 확인
        for check_path in [Path(video_path), PROJECT_ROOT / video_path, Path("/app") / video_path]:
            if check_path.exists():
                existing_results["video"] = {
                    "path": str(check_path),
                    "size_mb": round(check_path.stat().st_size / 1024 / 1024, 2),
                    "exists": True,
                }
                break
        else:
            existing_results["video"] = {"path": video_path, "exists": False}

    # 오디오 파일 확인
    if audio_path_str:
        for check_path in [Path(audio_path_str), PROJECT_ROOT / audio_path_str.lstrip("/app/")]:
            if check_path.exists():
                existing_results["audio"] = {
                    "path": str(check_path),
                    "size_mb": round(check_path.stat().st_size / 1024 / 1024, 2),
                    "exists": True,
                }
                break
        else:
            existing_results["audio"] = {"path": audio_path_str, "exists": False}

# ComfyUI 상태
import requests
try:
    comfy_resp = requests.get("http://localhost:8188/system_stats", timeout=5)
    comfy_status = comfy_resp.json() if comfy_resp.ok else {"error": comfy_resp.status_code}
except Exception as e:
    comfy_status = {"error": str(e)}

# Fish Speech 상태
try:
    fish_resp = requests.get("http://localhost:8080/v1/health", timeout=5)
    fish_status = "healthy" if fish_resp.ok else f"unhealthy ({fish_resp.status_code})"
except Exception as e:
    fish_status = f"unreachable ({e})"

# Ollama 상태
try:
    ollama_resp = requests.get("http://localhost:11434/api/tags", timeout=5)
    ollama_models = [m.get("name", "") for m in ollama_resp.json().get("models", [])] if ollama_resp.ok else []
    ollama_status = f"healthy ({len(ollama_models)} models)"
except Exception as e:
    ollama_status = f"unreachable ({e})"

RESULTS["phase7_8"] = {
    "existing_results": existing_results,
    "service_status": {
        "comfyui": comfy_status,
        "fish_speech": fish_status,
        "ollama": ollama_status,
        "ollama_models": ollama_models if "ollama_models" in dir() else [],
    },
    "post_status": str(post.status),
}

logger.info("기존 결과물: %s", json.dumps(existing_results, ensure_ascii=False, default=str))
logger.info("서비스 상태: ComfyUI=%s, Fish=%s, Ollama=%s",
            "OK" if isinstance(comfy_status, dict) and "error" not in comfy_status else comfy_status,
            fish_status, ollama_status)


# ─────────────────────────────────────────────────────────────────────
# 결과 저장
# ─────────────────────────────────────────────────────────────────────
banner("SAVE", "결과 저장")

result_json_path = OUTPUT_DIR / "pipeline_test_raw.json"
with open(result_json_path, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2, default=str)
logger.info("JSON 결과 저장: %s", result_json_path)

print("\n" + "=" * 70)
print("  파이프라인 테스트 완료!")
print("=" * 70)
print(f"  결과 JSON: {result_json_path}")
print(f"  Phase 1 (리소스 분석):  전략={profile.strategy}, 비율={profile.ratio}")
print(f"  Phase 2 (LLM 청킹):    hook='{llm_raw.get('hook', '')}', body={len(llm_raw.get('body', []))}개")
print(f"  Phase 3 (텍스트 검증):  위반={len(violations)}건, 변경={len(changes)+len(body_changes)}건")
print(f"  Phase 4 (씬 배분):      {len(scenes)}씬 — {type_counts}")
print(f"  Phase 4.5 (비디오 모드): {mode_counts}")
print(f"  Phase 5 (TTS):          {sum(1 for r in tts_results if r.get('success'))}건 성공")
print(f"  Phase 6 (프롬프트):     {len([r for r in prompt_results if 'video_prompt' in r])}건 생성")
print(f"  Phase 7-8:              상태={post.status}")
print("=" * 70)
