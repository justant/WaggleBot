"""E2E LTX-Video 파이프라인 테스트 스크립트.

Docker 컨테이너 안에서 실행:
    docker compose exec ai_worker python /app/test/e2e_video_test.py step0
    docker compose exec ai_worker python /app/test/e2e_video_test.py all

산출물 저장 위치: /app/test/test_e2e/{post_id}/
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path

# /app 을 sys.path에 추가 (Docker 컨테이너 내 모듈 import 해결)
sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("e2e_test")

POST_IDS = [903]
OUTPUT_BASE = Path("/app/test/test_e2e")


def ensure_output_dir(post_id: int) -> Path:
    d = OUTPUT_BASE / str(post_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(data, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info("저장: %s (%d bytes)", path, path.stat().st_size)


def save_text(text: str, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("저장: %s (%d bytes)", path, path.stat().st_size)


# ============================================================================
# Step 0: 환경 점검
# ============================================================================

def step0() -> dict:
    """환경 점검: DB, Ollama, ComfyUI, FFmpeg, GPU."""
    logger.info("=" * 60)
    logger.info("Step 0: 환경 점검")
    logger.info("=" * 60)

    results = {}

    # 1. DB 연결
    logger.info("[1/5] DB 연결 확인...")
    try:
        from db.session import SessionLocal
        from db.models import Post, Content, Comment
        with SessionLocal() as db:
            for pid in POST_IDS:
                post = db.query(Post).filter_by(id=pid).first()
                content = db.query(Content).filter_by(post_id=pid).first()
                comment_count = db.query(Comment).filter_by(post_id=pid).count()
                if post is None:
                    raise ValueError(f"post_id={pid} 없음")
                logger.info(
                    "  post_id=%d: title='%s', content=%s, comments=%d",
                    pid, post.title[:40], content is not None, comment_count,
                )
        results["db"] = "OK"
    except Exception as e:
        results["db"] = f"FAIL: {e}"
        logger.error("DB 연결 실패: %s", e)

    # 2. Ollama 연결
    logger.info("[2/5] Ollama 연결 확인...")
    try:
        from ai_worker.llm.client import call_ollama_raw
        resp = call_ollama_raw("Hello, respond with 'OK' only.", max_tokens=10)
        logger.info("  Ollama 응답: %s", resp.strip()[:50])
        results["ollama"] = "OK"
    except Exception as e:
        results["ollama"] = f"FAIL: {e}"
        logger.error("Ollama 연결 실패: %s", e)

    # 3. ComfyUI 연결
    logger.info("[3/5] ComfyUI 연결 확인...")
    try:
        from ai_worker.video.comfy_client import ComfyUIClient
        comfyui_url = os.environ.get("COMFYUI_URL", "http://comfyui:8188")
        client = ComfyUIClient(comfyui_url)
        healthy = asyncio.run(client.health_check())
        if healthy:
            results["comfyui"] = "OK"
            logger.info("  ComfyUI: 정상")
        else:
            results["comfyui"] = "FAIL: health_check returned False"
            logger.warning("  ComfyUI: 다운 상태")
    except Exception as e:
        results["comfyui"] = f"FAIL: {e}"
        logger.error("ComfyUI 연결 실패: %s", e)

    # 4. FFmpeg
    logger.info("[4/5] FFmpeg 확인...")
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10,
        )
        version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        logger.info("  FFmpeg: %s", version_line)
        results["ffmpeg"] = "OK"
    except Exception as e:
        results["ffmpeg"] = f"FAIL: {e}"
        logger.error("FFmpeg 실패: %s", e)

    # 5. GPU
    logger.info("[5/5] GPU 확인...")
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        gpu_info = result.stdout.strip()
        logger.info("  GPU: %s", gpu_info)
        results["gpu"] = "OK"
    except Exception as e:
        results["gpu"] = f"FAIL: {e}"
        logger.error("GPU 확인 실패: %s", e)

    # 결과 요약
    logger.info("-" * 40)
    all_ok = all(v == "OK" for v in results.values())
    for k, v in results.items():
        status = "✓" if v == "OK" else "✗"
        logger.info("  %s %s: %s", status, k, v)

    if not all_ok:
        failed = [k for k, v in results.items() if v != "OK"]
        logger.warning("환경 점검 실패 항목: %s", failed)
    else:
        logger.info("모든 환경 점검 통과")

    return results


# ============================================================================
# Step 1: 원본 데이터 추출
# ============================================================================

def step1() -> dict[int, dict]:
    """DB에서 원본 데이터를 추출하여 JSON으로 저장."""
    logger.info("=" * 60)
    logger.info("Step 1: 원본 데이터 추출")
    logger.info("=" * 60)

    from db.session import SessionLocal
    from db.models import Post, Content, Comment

    all_data = {}

    with SessionLocal() as db:
        for pid in POST_IDS:
            logger.info("post_id=%d 데이터 추출...", pid)
            post = db.query(Post).filter_by(id=pid).first()
            content = db.query(Content).filter_by(post_id=pid).first()
            comments = (
                db.query(Comment)
                .filter_by(post_id=pid)
                .order_by(Comment.likes.desc())
                .all()
            )

            data = {
                "post_id": pid,
                "title": post.title,
                "body": post.content,
                "images": post.images if post.images else [],
                "site_code": post.site_code,
                "origin_id": post.origin_id,
                "engagement_score": float(post.engagement_score or 0),
                "comments": [
                    {
                        "author": c.author,
                        "content": c.content,
                        "likes": c.likes,
                    }
                    for c in comments
                ],
                "content_summary": content.summary_text[:200] if content and content.summary_text else None,
            }

            out_dir = ensure_output_dir(pid)
            save_json(data, out_dir / "01_raw_data.json")

            logger.info(
                "  title='%s', body=%d자, images=%d, comments=%d",
                post.title[:40],
                len(post.content or ""),
                len(data["images"]),
                len(data["comments"]),
            )
            all_data[pid] = data

    return all_data


# ============================================================================
# Step 2: LLM 대본 생성
# ============================================================================

def step2(raw_data: dict[int, dict] | None = None) -> dict[int, dict]:
    """LLM으로 대본 생성."""
    logger.info("=" * 60)
    logger.info("Step 2: LLM 대본 생성")
    logger.info("=" * 60)

    from ai_worker.llm.client import generate_script

    if raw_data is None:
        raw_data = {}
        for pid in POST_IDS:
            path = OUTPUT_BASE / str(pid) / "01_raw_data.json"
            with open(path, encoding="utf-8") as f:
                raw_data[pid] = json.load(f)

    all_scripts = {}

    for pid in POST_IDS:
        logger.info("post_id=%d 대본 생성 중...", pid)
        data = raw_data[pid]

        comment_strs = [
            f"{c['author']}: {c['content']}" for c in data["comments"][:5]
        ]

        start = time.time()
        script = generate_script(
            title=data["title"],
            body=data["body"] or "",
            comments=comment_strs,
            post_id=pid,
        )
        elapsed = time.time() - start

        # ScriptData를 dict로 변환
        script_dict = {
            "hook": script.hook,
            "body": script.body,
            "closer": script.closer,
            "title_suggestion": script.title_suggestion,
            "tags": script.tags,
            "mood": script.mood,
        }

        out_dir = ensure_output_dir(pid)
        save_json(script_dict, out_dir / "02_script.json")

        # 사람이 읽을 수 있는 텍스트 버전
        readable_lines = []
        readable_lines.append(f"=== post_id={pid} 대본 ===")
        readable_lines.append(f"생성 시간: {elapsed:.1f}초")
        readable_lines.append(f"Mood: {script.mood}")
        readable_lines.append(f"Tags: {', '.join(script.tags)}")
        readable_lines.append(f"\n[HOOK]\n{script.hook}")
        readable_lines.append(f"\n[BODY] ({len(script.body)}개 블록)")
        for i, block in enumerate(script.body):
            block_type = block.get("type", "body")
            author = block.get("author", "")
            lines = block.get("lines", [])
            prefix = f"  [{i+1}] ({block_type}"
            if author:
                prefix += f", {author}"
            prefix += ")"
            readable_lines.append(prefix)
            for line in lines:
                readable_lines.append(f"    > {line}")
        readable_lines.append(f"\n[CLOSER]\n{script.closer}")
        readable_text = "\n".join(readable_lines)

        save_text(readable_text, out_dir / "02_script_readable.txt")

        logger.info(
            "  mood=%s, body=%d블록, tags=%s, elapsed=%.1fs",
            script.mood, len(script.body), script.tags, elapsed,
        )
        all_scripts[pid] = script_dict

    return all_scripts


# ============================================================================
# Step 3: 씬 배분 + video_mode 할당
# ============================================================================

def step3(
    raw_data: dict[int, dict] | None = None,
    scripts: dict[int, dict] | None = None,
) -> dict[int, list]:
    """씬 배분 및 video_mode 할당."""
    logger.info("=" * 60)
    logger.info("Step 3: 씬 배분 + video_mode 할당")
    logger.info("=" * 60)

    from ai_worker.pipeline.resource_analyzer import analyze_resources, ResourceProfile
    from ai_worker.pipeline.scene_director import SceneDirector, assign_video_modes

    if raw_data is None:
        raw_data = {}
        for pid in POST_IDS:
            path = OUTPUT_BASE / str(pid) / "01_raw_data.json"
            with open(path, encoding="utf-8") as f:
                raw_data[pid] = json.load(f)

    if scripts is None:
        scripts = {}
        for pid in POST_IDS:
            path = OUTPUT_BASE / str(pid) / "02_script.json"
            with open(path, encoding="utf-8") as f:
                scripts[pid] = json.load(f)

    all_scenes = {}

    for pid in POST_IDS:
        logger.info("post_id=%d 씬 배분...", pid)
        data = raw_data[pid]
        script = scripts[pid]
        images = data.get("images", [])

        # ResourceProfile 생성을 위한 간이 Post 객체
        class FakePost:
            def __init__(self, content_text):
                self.content = content_text
        fake_post = FakePost(data.get("body", ""))

        profile = analyze_resources(fake_post, images)
        logger.info(
            "  리소스 분석: strategy=%s, images=%d, text=%d자, ratio=%.3f",
            profile.strategy, profile.image_count, profile.text_length, profile.ratio,
        )

        # SceneDirector
        director = SceneDirector(
            profile=profile,
            images=images,
            script=script,
            mood=script.get("mood", "daily"),
        )
        scenes = director.direct()
        logger.info("  씬 배분 완료: %d개 씬", len(scenes))

        # video_mode 할당
        image_cache_dir = OUTPUT_BASE / str(pid) / "image_cache"
        image_cache_dir.mkdir(parents=True, exist_ok=True)
        scenes = assign_video_modes(scenes, image_cache_dir)

        # 씬 정보를 dict로 변환
        scenes_data = []
        for i, scene in enumerate(scenes):
            scene_dict = {
                "index": i,
                "type": scene.type,
                "video_mode": scene.video_mode,
                "text_lines": scene.text_lines,
                "image_url": scene.image_url,
                "video_init_image": scene.video_init_image,
                "mood": scene.mood,
                "tts_emotion": scene.tts_emotion,
                "block_type": scene.block_type,
                "author": scene.author,
                "bgm_path": scene.bgm_path,
            }
            scenes_data.append(scene_dict)

        out_dir = ensure_output_dir(pid)
        save_json(scenes_data, out_dir / "03_scenes.json")

        # 읽기 쉬운 테이블 형태
        readable_lines = []
        readable_lines.append(f"=== post_id={pid} 씬 배분 ===")
        readable_lines.append(f"전략: {profile.strategy}")
        readable_lines.append(f"총 씬: {len(scenes)}개")
        readable_lines.append("")
        readable_lines.append(
            f"{'#':>3} | {'Type':>10} | {'Video':>5} | {'Block':>8} | {'Image':>5} | Text"
        )
        readable_lines.append("-" * 80)
        t2v_count = 0
        i2v_count = 0
        for i, s in enumerate(scenes_data):
            vm = s["video_mode"] or "-"
            has_img = "Y" if s["image_url"] else "N"
            text_preview = ""
            for tl in s["text_lines"]:
                if isinstance(tl, dict):
                    text_preview = tl.get("text", "")[:40]
                else:
                    text_preview = str(tl)[:40]
                break
            readable_lines.append(
                f"{i:>3} | {s['type']:>10} | {vm:>5} | {s['block_type']:>8} | {has_img:>5} | {text_preview}"
            )
            if vm == "t2v":
                t2v_count += 1
            elif vm == "i2v":
                i2v_count += 1
        readable_lines.append("")
        readable_lines.append(f"T2V: {t2v_count}개, I2V: {i2v_count}개")
        save_text("\n".join(readable_lines), out_dir / "03_scenes_readable.txt")

        logger.info("  T2V=%d, I2V=%d", t2v_count, i2v_count)
        all_scenes[pid] = scenes  # SceneDecision 객체 리스트 유지

    return all_scenes


# ============================================================================
# Step 4: Video Prompt 생성
# ============================================================================

def step4(
    all_scenes: dict[int, list] | None = None,
    scripts: dict[int, dict] | None = None,
    raw_data: dict[int, dict] | None = None,
) -> dict[int, list]:
    """Video Prompt 생성."""
    logger.info("=" * 60)
    logger.info("Step 4: Video Prompt 생성")
    logger.info("=" * 60)

    from ai_worker.video.prompt_engine import VideoPromptEngine

    if scripts is None:
        scripts = {}
        for pid in POST_IDS:
            path = OUTPUT_BASE / str(pid) / "02_script.json"
            with open(path, encoding="utf-8") as f:
                scripts[pid] = json.load(f)

    if raw_data is None:
        raw_data = {}
        for pid in POST_IDS:
            path = OUTPUT_BASE / str(pid) / "01_raw_data.json"
            with open(path, encoding="utf-8") as f:
                raw_data[pid] = json.load(f)

    # all_scenes가 없으면 Step 3 재실행
    if all_scenes is None:
        all_scenes = step3(raw_data, scripts)

    engine = VideoPromptEngine()

    for pid in POST_IDS:
        logger.info("post_id=%d Video Prompt 생성...", pid)
        scenes = all_scenes[pid]
        script = scripts[pid]
        data = raw_data[pid]

        mood = script.get("mood", "daily")
        title = data["title"]
        body_summary = (data.get("body") or "")[:500]

        # generate_batch 호출
        scenes = engine.generate_batch(
            scenes=scenes,
            mood=mood,
            title=title,
            body_summary=body_summary,
        )

        # 프롬프트 저장
        prompts_data = []
        success_count = 0
        fail_count = 0
        for i, scene in enumerate(scenes):
            vm = getattr(scene, "video_mode", None)
            if vm not in ("t2v", "i2v"):
                continue
            prompt = getattr(scene, "video_prompt", None)
            if prompt:
                success_count += 1
            else:
                fail_count += 1

            # 원본 한국어 텍스트 추출
            ko_text = ""
            for tl in scene.text_lines:
                if isinstance(tl, dict):
                    ko_text += tl.get("text", "") + " "
                else:
                    ko_text += str(tl) + " "

            prompts_data.append({
                "scene_index": i,
                "video_mode": vm,
                "korean_text": ko_text.strip(),
                "video_prompt": prompt,
            })

        out_dir = ensure_output_dir(pid)
        save_json(prompts_data, out_dir / "04_video_prompts.json")

        # 읽기 쉬운 대조 형태
        readable_lines = []
        readable_lines.append(f"=== post_id={pid} Video Prompts ===")
        readable_lines.append(f"성공: {success_count}, 실패: {fail_count}")
        readable_lines.append("")
        for p in prompts_data:
            readable_lines.append(f"--- 씬 {p['scene_index']} ({p['video_mode']}) ---")
            readable_lines.append(f"[한국어] {p['korean_text']}")
            readable_lines.append(f"[영어 프롬프트] {p['video_prompt'] or '(생성 실패)'}")
            readable_lines.append("")
        save_text("\n".join(readable_lines), out_dir / "04_video_prompts_readable.txt")

        logger.info("  프롬프트 생성: 성공=%d, 실패=%d", success_count, fail_count)
        all_scenes[pid] = scenes

    return all_scenes


# ============================================================================
# Step 5: LTX-Video 클립 생성
# ============================================================================

def _try_load_scenes_from_json() -> dict[int, list] | None:
    """기존 Step 3/4 산출물에서 SceneDecision 객체를 복원. 하나라도 없으면 None 반환."""
    from ai_worker.pipeline.scene_director import SceneDecision

    all_scenes: dict[int, list] = {}
    for pid in POST_IDS:
        scenes_path = OUTPUT_BASE / str(pid) / "03_scenes.json"
        prompts_path = OUTPUT_BASE / str(pid) / "04_video_prompts.json"
        if not scenes_path.exists() or not prompts_path.exists():
            return None

        with open(scenes_path, encoding="utf-8") as f:
            scenes_data = json.load(f)
        with open(prompts_path, encoding="utf-8") as f:
            prompts_data = json.load(f)

        # 프롬프트를 인덱스별로 매핑
        prompt_map = {p["scene_index"]: p.get("video_prompt") for p in prompts_data}

        scenes = []
        for i, s in enumerate(scenes_data):
            scene = SceneDecision(
                type=s["type"],
                text_lines=s.get("text_lines", []),
                image_url=s.get("image_url"),
                video_mode=s.get("video_mode"),
                video_init_image=s.get("video_init_image"),
                video_prompt=prompt_map.get(i) or s.get("video_prompt"),
                mood=s.get("mood", "daily"),
            )
            scenes.append(scene)
        all_scenes[pid] = scenes
        logger.info("post_id=%d 기존 산출물에서 %d개 씬 로드 완료", pid, len(scenes))

    return all_scenes


def step5(all_scenes: dict[int, list] | None = None) -> dict[int, list]:
    """ComfyUI를 통해 비디오 클립 생성."""
    logger.info("=" * 60)
    logger.info("Step 5: LTX-Video 클립 생성")
    logger.info("=" * 60)

    from ai_worker.video.comfy_client import ComfyUIClient
    from ai_worker.video.prompt_engine import VideoPromptEngine
    from ai_worker.video.manager import VideoManager

    if all_scenes is None:
        # 기존 Step 4 결과에서 씬 로드 시도 (재실행 방지)
        all_scenes = _try_load_scenes_from_json()
        if all_scenes is None:
            # Step 3, 4 재실행
            all_scenes = step4()

    comfyui_url = os.environ.get("COMFYUI_URL", "http://comfyui:8188")
    comfy_client = ComfyUIClient(
        base_url=comfyui_url,
        output_dir=Path(os.environ.get("VIDEO_OUTPUT_DIR", "/app/media/tmp/videos")),
    )
    prompt_engine = VideoPromptEngine()
    video_config = {
        "VIDEO_RESOLUTION": (512, 512),
        "VIDEO_NUM_FRAMES": 81,
        "VIDEO_RESOLUTION_FALLBACK": (384, 384),
        "VIDEO_NUM_FRAMES_FALLBACK": 61,
        "VIDEO_MAX_CLIPS_PER_POST": 8,
        "VIDEO_MAX_RETRY": 3,
    }
    manager = VideoManager(comfy_client, prompt_engine, video_config)

    for pid in POST_IDS:
        logger.info("post_id=%d 비디오 클립 생성...", pid)
        scenes = all_scenes[pid]

        # 원본 데이터에서 title, body_summary 가져오기
        raw_path = OUTPUT_BASE / str(pid) / "01_raw_data.json"
        with open(raw_path, encoding="utf-8") as f:
            raw = json.load(f)

        start = time.time()
        scenes = asyncio.run(
            manager.generate_all_clips(
                scenes=scenes,
                mood=raw.get("mood", "daily"),
                post_id=pid,
                title=raw["title"],
                body_summary=(raw.get("body") or "")[:500],
            )
        )
        elapsed = time.time() - start

        # 클립 파일을 산출물 디렉토리로 복사
        out_dir = ensure_output_dir(pid)
        clips_dir = out_dir / "05_video_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        clip_results = []
        import shutil
        for i, scene in enumerate(scenes):
            clip_path = getattr(scene, "video_clip_path", None)
            failed = getattr(scene, "video_generation_failed", False)
            result_entry = {
                "scene_index": i,
                "video_mode": getattr(scene, "video_mode", None),
                "success": clip_path is not None and not failed,
                "clip_path": clip_path,
                "failed": failed,
            }
            if clip_path and Path(clip_path).exists():
                dest = clips_dir / f"scene_{i:02d}_{scene.video_mode or 'none'}.mp4"
                shutil.copy2(clip_path, dest)
                result_entry["saved_to"] = str(dest)
                logger.info("  씬 %d: 클립 저장 → %s", i, dest.name)
            clip_results.append(result_entry)

        save_json(clip_results, out_dir / "05_clip_results.json")

        success_clips = sum(1 for r in clip_results if r["success"])
        failed_clips = sum(1 for r in clip_results if r.get("failed"))
        logger.info(
            "  post_id=%d 클립 생성 완료: 성공=%d, 실패=%d, 소요=%.1fs",
            pid, success_clips, failed_clips, elapsed,
        )
        all_scenes[pid] = scenes

    return all_scenes


# ============================================================================
# Step 6: 최종 영상 합성
# ============================================================================

def step6(all_scenes: dict[int, list] | None = None) -> dict[int, str]:
    """최종 쇼츠 영상 합성."""
    logger.info("=" * 60)
    logger.info("Step 6: 최종 영상 합성")
    logger.info("=" * 60)

    from ai_worker.renderer.layout import render_layout_video_from_scenes
    from db.session import SessionLocal
    from db.models import Post

    if all_scenes is None:
        all_scenes = step5()

    results = {}

    for pid in POST_IDS:
        logger.info("post_id=%d 최종 영상 합성...", pid)
        scenes = all_scenes[pid]
        out_dir = ensure_output_dir(pid)
        output_path = out_dir / "06_final_video.mp4"

        with SessionLocal() as db:
            post = db.query(Post).filter_by(id=pid).first()

            start = time.time()
            try:
                result_path = render_layout_video_from_scenes(
                    post=post,
                    scenes=scenes,
                    output_path=output_path,
                )
                elapsed = time.time() - start

                # 영상 정보 확인
                import subprocess
                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "quiet",
                        "-print_format", "json",
                        "-show_format", "-show_streams",
                        str(result_path),
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                probe_data = json.loads(probe.stdout) if probe.stdout else {}
                duration = float(probe_data.get("format", {}).get("duration", 0))
                size_bytes = int(probe_data.get("format", {}).get("size", 0))

                render_log = [
                    f"=== post_id={pid} 렌더링 로그 ===",
                    f"출력 파일: {result_path}",
                    f"영상 길이: {duration:.1f}초",
                    f"파일 크기: {size_bytes / 1024 / 1024:.2f} MB",
                    f"렌더링 소요: {elapsed:.1f}초",
                    f"총 씬 수: {len(scenes)}",
                    f"비디오 씬: {sum(1 for s in scenes if getattr(s, 'video_clip_path', None))}",
                    f"정적 씬: {sum(1 for s in scenes if not getattr(s, 'video_clip_path', None))}",
                ]
                save_text("\n".join(render_log), out_dir / "06_render_log.txt")

                logger.info(
                    "  post_id=%d 렌더링 완료: %.1f초, %.2fMB, elapsed=%.1fs",
                    pid, duration, size_bytes / 1024 / 1024, elapsed,
                )
                results[pid] = str(result_path)
            except Exception as e:
                elapsed = time.time() - start
                logger.error("  post_id=%d 렌더링 실패: %s", pid, e)
                traceback.print_exc()
                render_log = [
                    f"=== post_id={pid} 렌더링 로그 (실패) ===",
                    f"에러: {e}",
                    f"소요: {elapsed:.1f}초",
                    f"트레이스백:\n{traceback.format_exc()}",
                ]
                save_text("\n".join(render_log), out_dir / "06_render_log.txt")
                results[pid] = f"FAIL: {e}"

    return results


# ============================================================================
# Step 7: 결과 리포트
# ============================================================================

def step7():
    """전체 결과를 마크다운 리포트로 정리."""
    logger.info("=" * 60)
    logger.info("Step 7: 결과 리포트 작성")
    logger.info("=" * 60)

    report_lines = ["# E2E LTX-Video 테스트 리포트\n"]
    report_lines.append(f"**실행일시:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    bugfixes = []

    for pid in POST_IDS:
        out_dir = OUTPUT_BASE / str(pid)
        report_lines.append(f"\n## post_id: {pid}\n")

        # Step 1: 원본 데이터
        raw_path = out_dir / "01_raw_data.json"
        if raw_path.exists():
            with open(raw_path, encoding="utf-8") as f:
                raw = json.load(f)
            report_lines.append(f"### 원본 데이터")
            report_lines.append(f"- 제목: {raw['title']}")
            report_lines.append(f"- 본문 길이: {len(raw.get('body', '') or '')}자")
            report_lines.append(f"- 이미지: {len(raw.get('images', []))}개")
            report_lines.append(f"- 댓글: {len(raw.get('comments', []))}개")
            report_lines.append(f"- 사이트: {raw.get('site_code', 'N/A')}\n")
        else:
            report_lines.append("### 원본 데이터\n- 파일 없음\n")

        # Step 2: 대본
        script_path = out_dir / "02_script.json"
        if script_path.exists():
            with open(script_path, encoding="utf-8") as f:
                script = json.load(f)
            report_lines.append(f"### 대본")
            report_lines.append(f"- Mood: {script.get('mood', 'N/A')}")
            report_lines.append(f"- 블록 수: {len(script.get('body', []))}")
            report_lines.append(f"- Tags: {', '.join(script.get('tags', []))}\n")
        else:
            report_lines.append("### 대본\n- 파일 없음\n")

        # Step 3: 씬 배분
        scenes_path = out_dir / "03_scenes.json"
        if scenes_path.exists():
            with open(scenes_path, encoding="utf-8") as f:
                scenes = json.load(f)
            t2v = sum(1 for s in scenes if s.get("video_mode") == "t2v")
            i2v = sum(1 for s in scenes if s.get("video_mode") == "i2v")
            report_lines.append(f"### 씬 배분")
            report_lines.append(f"- 총 씬: {len(scenes)}개")
            report_lines.append(f"- T2V: {t2v}개")
            report_lines.append(f"- I2V: {i2v}개\n")
        else:
            report_lines.append("### 씬 배분\n- 파일 없음\n")

        # Step 4: 프롬프트
        prompts_path = out_dir / "04_video_prompts.json"
        if prompts_path.exists():
            with open(prompts_path, encoding="utf-8") as f:
                prompts = json.load(f)
            success = sum(1 for p in prompts if p.get("video_prompt"))
            total = len(prompts)
            report_lines.append(f"### Video Prompt 생성")
            report_lines.append(f"- 성공: {success}/{total}")
            report_lines.append(f"- 성공률: {success/total*100:.0f}%\n" if total > 0 else "- 성공률: N/A\n")
        else:
            report_lines.append("### Video Prompt 생성\n- 파일 없음\n")

        # Step 5: 클립 생성
        clips_path = out_dir / "05_clip_results.json"
        if clips_path.exists():
            with open(clips_path, encoding="utf-8") as f:
                clips = json.load(f)
            success = sum(1 for c in clips if c.get("success"))
            total = len([c for c in clips if c.get("video_mode") in ("t2v", "i2v")])
            report_lines.append(f"### 클립 생성")
            report_lines.append(f"- 성공: {success}/{total}")
            report_lines.append(f"- 성공률: {success/total*100:.0f}%\n" if total > 0 else "- 성공률: N/A\n")
        else:
            report_lines.append("### 클립 생성\n- 파일 없음\n")

        # Step 6: 최종 영상
        final_path = out_dir / "06_final_video.mp4"
        render_log_path = out_dir / "06_render_log.txt"
        if final_path.exists():
            import subprocess
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    str(final_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            probe_data = json.loads(probe.stdout) if probe.stdout else {}
            duration = float(probe_data.get("format", {}).get("duration", 0))
            size_mb = int(probe_data.get("format", {}).get("size", 0)) / 1024 / 1024
            passed = duration >= 15.0
            report_lines.append(f"### 최종 영상")
            report_lines.append(f"- 파일: {final_path.name}")
            report_lines.append(f"- 길이: {duration:.1f}초 {'✓' if passed else '✗ (15초 미만)'}")
            report_lines.append(f"- 크기: {size_mb:.2f} MB\n")
        elif render_log_path.exists():
            log_text = render_log_path.read_text(encoding="utf-8")
            report_lines.append(f"### 최종 영상\n- 렌더링 실패\n```\n{log_text}\n```\n")
        else:
            report_lines.append("### 최종 영상\n- 파일 없음\n")

    # 성공 기준 체크
    report_lines.append("\n## 성공 기준 체크\n")
    all_pass = True
    for pid in POST_IDS:
        final_path = OUTPUT_BASE / str(pid) / "06_final_video.mp4"
        exists = final_path.exists()
        if exists:
            import subprocess
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    str(final_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            probe_data = json.loads(probe.stdout) if probe.stdout else {}
            duration = float(probe_data.get("format", {}).get("duration", 0))
            dur_ok = duration >= 15.0
        else:
            dur_ok = False
        passed = exists and dur_ok
        if not passed:
            all_pass = False
        report_lines.append(
            f"- post_id={pid}: 파일={'✓' if exists else '✗'}, "
            f"길이={'✓ %.1fs' % duration if exists else '✗'} "
            f"→ **{'PASS' if passed else 'FAIL'}**"
        )

    report_lines.append(f"\n**최종 결과: {'ALL PASS ✓' if all_pass else 'FAIL ✗'}**\n")

    # 리포트 저장
    report_text = "\n".join(report_lines)
    save_text(report_text, OUTPUT_BASE / "E2E_TEST_REPORT.md")
    logger.info("리포트 저장: %s", OUTPUT_BASE / "E2E_TEST_REPORT.md")


# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python e2e_video_test.py <step0|step1|step2|step3|step4|step5|step6|step7|all>")
        sys.exit(1)

    step = sys.argv[1].lower()

    if step == "step0":
        step0()
    elif step == "step1":
        step1()
    elif step == "step2":
        step2()
    elif step == "step3":
        step3()
    elif step == "step4":
        step4()
    elif step == "step5":
        step5()
    elif step == "step6":
        step6()
    elif step == "step7":
        step7()
    elif step == "all":
        env = step0()
        # ComfyUI 다운이어도 Step 1~4는 진행
        raw_data = step1()
        scripts = step2(raw_data)
        all_scenes = step3(raw_data, scripts)
        all_scenes = step4(all_scenes, scripts, raw_data)

        # ComfyUI 필수 단계
        if env.get("comfyui") == "OK":
            all_scenes = step5(all_scenes)
            step6(all_scenes)
        else:
            logger.error("ComfyUI 다운 — Step 5, 6 실행 불가")
            logger.error("ComfyUI 컨테이너를 확인하세요: docker compose up -d comfyui")
            # 그래도 Step 6는 비디오 클립 없이 시도
            step6(all_scenes)

        step7()
    else:
        print(f"Unknown step: {step}")
        sys.exit(1)


if __name__ == "__main__":
    main()
