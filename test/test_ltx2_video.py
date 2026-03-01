"""LTX-2 ComfyUI 종합 테스트 스크립트.

사용법:
    # 전체 테스트 실행
    python -m test.test_ltx2_video --all

    # 특정 카테고리만 실행
    python -m test.test_ltx2_video --quality          # 화질 비교 (Distilled vs Upscale vs Full)
    python -m test.test_ltx2_video --audio            # TTS 비교 (Fish Speech vs LTX-2 내장)
    python -m test.test_ltx2_video --i2v              # I2V vs T2V 비교
    python -m test.test_ltx2_video --person           # 인물 Tier 테스트
    python -m test.test_ltx2_video --params           # 파라미터 스윕
    python -m test.test_ltx2_video --seed             # 시드 일관성
    python -m test.test_ltx2_video --negative         # 네거티브 프롬프트 효과
    python -m test.test_ltx2_video --oom              # OOM 스트레스
    python -m test.test_ltx2_video --mood             # Mood별 스타일
    python -m test.test_ltx2_video --integration      # 전체 파이프라인

    # 단일 테스트 실행
    python -m test.test_ltx2_video --single Q-D-1

결과 출력: media/test_outputs/{category}/{test_id}/
    ├── {test_id}.mp4
    ├── {test_id}.wav (해당시)
    ├── {test_id}_meta.json
    └── {test_id}_thumbnail.jpg

최종 요약: media/test_outputs/TEST_SUMMARY.md
"""

import argparse
import asyncio
import gc
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시 모듈 import 지원)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── 출력 디렉터리 ──
BASE_OUTPUT_DIR = Path("media/test_outputs")


def check_system_requirements() -> None:
    """시스템 요구사항 확인 (GPU, RAM, ComfyUI VRAM 모드)."""
    # GPU 확인
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        gpu_info = result.stdout.strip()
        logger.info("GPU: %s", gpu_info)
    except Exception as e:
        logger.warning("GPU 정보 조회 실패: %s", e)

    # 시스템 RAM 확인
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024**3)
        logger.info("System RAM: %.1f GB", ram_gb)
        if ram_gb < 32:
            logger.warning(
                "시스템 RAM이 %.1fGB로 32GB 미만입니다. "
                "LTX-2 weight streaming에 최소 32GB RAM 필요. "
                "OOM 또는 극심한 성능 저하 가능.", ram_gb,
            )
    except ImportError:
        logger.warning("psutil 미설치 — RAM 확인 스킵")

    # ComfyUI VRAM 상태 확인
    try:
        import httpx
        resp = httpx.get("http://comfyui:8188/system_stats", timeout=5.0)
        stats = resp.json()
        devices = stats.get("devices", [{}])
        if devices:
            vram_total = devices[0].get("vram_total", 0)
            vram_free = devices[0].get("vram_free", 0)
            logger.info("VRAM Total: %.1f GB", vram_total / 1e9)
            logger.info("VRAM Free: %.1f GB", vram_free / 1e9)
    except Exception as e:
        logger.warning("ComfyUI 시스템 상태 조회 실패: %s", e)

# ── 프롬프트 V2 네거티브 ──
NEGATIVE_PROMPT_V2 = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "watermarks, anime, cartoon, 3d render, CGI, "
    "cyberpunk, sci-fi, futuristic, neon lights, abstract, "
    "deformed hands, extra fingers, bad anatomy, "
    "ugly, duplicate, morbid, mutilated, "
    "western faces, caucasian, european setting"
)


# ====================================================================
# 테스트 데이터 정의
# ====================================================================

QUALITY_TEST_PROMPTS = [
    {
        "name": "korean_woman_cafe",
        "prompt": (
            "A beautiful Korean woman in her late twenties sits at a wooden table in a cozy Seoul cafe, "
            "gently stirring a latte with a small spoon. She wears a cream-colored knit sweater and her "
            "long dark hair falls softly over her shoulders. Warm afternoon sunlight streams through the "
            "window, casting golden highlights on her face. She looks up briefly and smiles slightly. "
            "The background shows blurred cafe interior with hanging plants. Ambient sounds of a coffee "
            "machine steaming and quiet background chatter."
        ),
        "mood": "daily",
    },
    {
        "name": "korean_man_office",
        "prompt": (
            "A handsome Korean man in his early thirties stands in a modern Seoul office, "
            "looking at documents on his desk with a focused expression. He wears a navy blue suit "
            "with a loosened tie. Fluorescent office lighting mixed with natural light from floor-to-ceiling "
            "windows showing the Gangnam skyline. He picks up his phone and checks it briefly. "
            "Camera at eye level, medium shot from waist up. Sound of keyboard typing and distant phone ringing."
        ),
        "mood": "daily",
    },
    {
        "name": "street_scene_seoul",
        "prompt": (
            "A bustling crosswalk in Hongdae, Seoul at golden hour. Dozens of young Korean pedestrians "
            "cross the street in both directions, some carrying shopping bags, others looking at phones. "
            "Neon signs of Korean restaurants and shops glow warmly in the background. A street musician "
            "plays acoustic guitar on the corner. Camera is static, wide shot capturing the full intersection. "
            "Sounds of footsteps, distant Korean pop music, and city traffic."
        ),
        "mood": "daily",
    },
]

AUDIO_TEST_CASES = [
    {
        "name": "narration_daily",
        "korean_script": "오늘 회사에서 정말 황당한 일이 있었어요. 팀장님이 갑자기 회의실로 부르더니...",
        "video_prompt_with_audio": (
            "A Korean woman in her twenties sits on a beige sofa in a modern apartment living room, "
            "speaking directly to the camera with animated expressions. She gestures with her hands "
            "as she tells a story, occasionally widening her eyes in surprise. Warm interior lighting. "
            "Medium shot, eye level. She speaks in Korean with an expressive, storytelling tone: "
            "'오늘 회사에서 정말 황당한 일이 있었어요. 팀장님이 갑자기 회의실로 부르더니...'"
        ),
        "video_prompt_no_audio": (
            "A Korean woman in her twenties sits on a beige sofa in a modern apartment living room, "
            "speaking directly to the camera with animated expressions. She gestures with her hands "
            "as she tells a story, occasionally widening her eyes in surprise. Warm interior lighting. "
            "Medium shot, eye level."
        ),
        "mood": "humor",
    },
    {
        "name": "narration_news",
        "korean_script": "최근 서울 강남역 인근에서 대규모 시위가 발생했습니다. 경찰 추산 약 3천명이...",
        "video_prompt_with_audio": (
            "A male Korean news anchor in his forties sits at a sleek news desk, "
            "reading from a teleprompter with a serious, authoritative expression. "
            "He wears a dark gray suit with a blue tie. Studio lighting is bright and even. "
            "News graphics visible on screens behind him. Camera static, medium close-up. "
            "He speaks in Korean with a formal news anchor tone: "
            "'최근 서울 강남역 인근에서 대규모 시위가 발생했습니다. 경찰 추산 약 3천명이...'"
        ),
        "video_prompt_no_audio": (
            "A male Korean news anchor in his forties sits at a sleek news desk, "
            "reading from a teleprompter with a serious, authoritative expression. "
            "He wears a dark gray suit with a blue tie. Studio lighting is bright and even. "
            "News graphics visible on screens behind him. Camera static, medium close-up."
        ),
        "mood": "info",
    },
    {
        "name": "narration_emotional",
        "korean_script": "그때 엄마가 저한테 이런 말을 하셨어요... 그 말을 듣는 순간 눈물이...",
        "video_prompt_with_audio": (
            "A young Korean woman in her twenties walks slowly along the Han River path at sunset. "
            "She looks down at the ground pensively, her expression showing quiet sadness. "
            "Cherry blossom petals drift in the warm breeze. Golden hour light creates long shadows. "
            "Camera follows her from the side in a slow tracking shot. "
            "A soft, emotional female Korean voice narrates: "
            "'그때 엄마가 저한테 이런 말을 하셨어요... 그 말을 듣는 순간 눈물이...'"
        ),
        "video_prompt_no_audio": (
            "A young Korean woman in her twenties walks slowly along the Han River path at sunset. "
            "She looks down at the ground pensively, her expression showing quiet sadness. "
            "Cherry blossom petals drift in the warm breeze. Golden hour light creates long shadows. "
            "Camera follows her from the side in a slow tracking shot."
        ),
        "mood": "touching",
    },
]

I2V_TEST_CASES = [
    {
        "name": "news_anchor_photo",
        "test_image": "test/fixtures/korean_anchor.jpg",
        "i2v_prompt": (
            "The news anchor begins speaking, subtle mouth movement and slight head nod. "
            "Eyes blink naturally. Studio lights create soft reflections. Camera static. "
            "Sound of authoritative Korean male voice speaking."
        ),
        "t2v_prompt": (
            "A Korean male news anchor in his forties at a news desk, speaking to camera "
            "with authoritative expression. Bright studio lighting, news graphics behind. "
            "Camera static, medium close-up. Sound of news broadcast."
        ),
    },
    {
        "name": "cafe_interior_photo",
        "test_image": "test/fixtures/seoul_cafe.jpg",
        "i2v_prompt": (
            "Steam gently rises from the coffee cup. Sunlight shifts slightly through the window. "
            "A hand reaches in from the right side to pick up the cup. "
            "Ambient sounds of cafe chatter and coffee machine."
        ),
        "t2v_prompt": (
            "Interior of a cozy Seoul cafe, warm wooden furniture, a steaming latte on a table. "
            "Sunlight through large windows, hanging plants sway gently. "
            "A Korean woman's hand reaches for the coffee cup. Camera static, close-up on table. "
            "Sounds of cafe ambience."
        ),
    },
    {
        "name": "street_photo",
        "test_image": "test/fixtures/seoul_street.jpg",
        "i2v_prompt": (
            "Pedestrians begin walking across the frame. Car headlights flicker in the background. "
            "Neon signs pulse gently. Subtle camera shake. "
            "City traffic sounds, distant Korean pop music from a shop."
        ),
        "t2v_prompt": (
            "A busy Seoul street at dusk, Korean pedestrians walking, neon signs of restaurants "
            "and shops glowing. Cars passing on the road. Camera static, wide shot. "
            "Sounds of city traffic and footsteps."
        ),
    },
]

PERSON_TIER_TESTS = [
    {
        "name": "tier1_medium_shot",
        "tier": 1,
        "prompt": (
            "A handsome Korean man in his thirties walks through a park in Seoul, "
            "wearing a casual white t-shirt and jeans. Medium shot from waist up, "
            "he's looking at his phone and smiling. Trees and a pond in the background. "
            "Bright afternoon sunlight. Birds chirping."
        ),
    },
    {
        "name": "tier1_side_profile",
        "tier": 1,
        "prompt": (
            "Side profile of a beautiful Korean woman in her twenties sitting by a window "
            "in a train. She gazes out the window as Korean countryside passes by. "
            "Natural window light illuminates her face. Camera static, close medium shot. "
            "Sound of train wheels on tracks."
        ),
    },
    {
        "name": "tier1_group_scene",
        "tier": 1,
        "prompt": (
            "Three Korean office workers in their twenties and thirties sit around a conference "
            "table having a meeting. Two men and one woman, all in business casual. "
            "One person gestures while speaking, others nod. Modern office interior. "
            "Camera static, wide medium shot. Muffled conversation sounds."
        ),
    },
    {
        "name": "tier1_close_face",
        "tier": 1,
        "prompt": (
            "Close-up of a young Korean woman's face as she reacts with surprise, "
            "eyes widening and mouth opening slightly. Soft indoor lighting. "
            "Shallow depth of field blurs the background. Camera static. "
            "A soft gasp sound."
        ),
    },
    {
        "name": "tier2_empty_room",
        "tier": 2,
        "prompt": (
            "An empty modern Korean office meeting room with a large monitor showing a presentation. "
            "A laptop sits open on the table with documents scattered around. "
            "Afternoon sunlight streams through blinds creating shadow patterns. "
            "Camera slowly pans across the desk. Quiet hum of air conditioning."
        ),
    },
    {
        "name": "tier2_hands_only",
        "tier": 2,
        "prompt": (
            "Close-up of Korean woman's hands typing rapidly on a laptop keyboard. "
            "Manicured nails, a small ring on her finger. Warm desk lamp light. "
            "A coffee cup and potted succulent visible at the edge of frame. "
            "Camera static. Sound of keyboard clicking."
        ),
    },
    {
        "name": "tier3_puppy_office",
        "tier": 3,
        "prompt": (
            "An adorable golden retriever puppy sits at a tiny desk in a miniature office setup, "
            "wearing a small blue tie. The puppy tilts its head and paws at a tiny laptop. "
            "Cute cartoon-style animation with warm pastel colors. "
            "Camera static, medium shot. Playful cheerful music."
        ),
    },
    {
        "name": "tier3_kitten_cafe",
        "tier": 3,
        "prompt": (
            "A fluffy white kitten sits on a cafe chair, batting at a tiny coffee cup with its paw. "
            "The kitten looks up with big round eyes. Cute animated style with soft lighting. "
            "Miniature cafe setting with Korean signage. "
            "Camera static. Gentle meowing and cafe ambience."
        ),
    },
]

PARAM_SWEEP_TESTS = [
    # 해상도 테스트
    {"name": "res_540p",   "width": 960,  "height": 540, "frames": 97, "steps": 20},
    {"name": "res_720p",   "width": 1280, "height": 720, "frames": 97, "steps": 20},
    {"name": "res_1080p",  "width": 1920, "height": 1080, "frames": 97, "steps": 20},
    # 프레임 수 테스트 (1+8k 규칙)
    {"name": "frames_25",  "width": 1280, "height": 720, "frames": 25, "steps": 20},
    {"name": "frames_49",  "width": 1280, "height": 720, "frames": 49, "steps": 20},
    {"name": "frames_97",  "width": 1280, "height": 720, "frames": 97, "steps": 20},
    {"name": "frames_121", "width": 1280, "height": 720, "frames": 121, "steps": 20},
    {"name": "frames_193", "width": 1280, "height": 720, "frames": 193, "steps": 20},
    # 스텝 수 테스트
    {"name": "steps_8",    "width": 1280, "height": 720, "frames": 97, "steps": 8},
    {"name": "steps_12",   "width": 1280, "height": 720, "frames": 97, "steps": 12},
    {"name": "steps_20",   "width": 1280, "height": 720, "frames": 97, "steps": 20},
    {"name": "steps_30",   "width": 1280, "height": 720, "frames": 97, "steps": 30},
    # CFG 테스트
    {"name": "cfg_1.0",    "width": 1280, "height": 720, "frames": 97, "steps": 20, "cfg": 1.0},
    {"name": "cfg_2.0",    "width": 1280, "height": 720, "frames": 97, "steps": 20, "cfg": 2.0},
    {"name": "cfg_3.5",    "width": 1280, "height": 720, "frames": 97, "steps": 20, "cfg": 3.5},
    {"name": "cfg_5.0",    "width": 1280, "height": 720, "frames": 97, "steps": 20, "cfg": 5.0},
    {"name": "cfg_7.0",    "width": 1280, "height": 720, "frames": 97, "steps": 20, "cfg": 7.0},
    # FPS 테스트
    {"name": "fps_15",     "width": 1280, "height": 720, "frames": 97, "steps": 20, "fps": 15},
    {"name": "fps_24",     "width": 1280, "height": 720, "frames": 97, "steps": 20, "fps": 24},
    {"name": "fps_30",     "width": 1280, "height": 720, "frames": 97, "steps": 20, "fps": 30},
]

SEED_TESTS = [
    {"name": "seed_fixed_a", "seed": 42},
    {"name": "seed_fixed_b", "seed": 42},
    {"name": "seed_random_a", "seed": -1},
    {"name": "seed_random_b", "seed": -1},
]

NEGATIVE_PROMPT_TESTS = [
    {
        "name": "with_negative",
        "prompt": QUALITY_TEST_PROMPTS[0]["prompt"],
        "negative": NEGATIVE_PROMPT_V2,
    },
    {
        "name": "without_negative",
        "prompt": QUALITY_TEST_PROMPTS[0]["prompt"],
        "negative": "",
    },
    {
        "name": "ambiguous_prompt_with_neg",
        "prompt": "A person walking in a city at night with bright lights and tall buildings. Footsteps on pavement.",
        "negative": NEGATIVE_PROMPT_V2,
    },
    {
        "name": "ambiguous_prompt_no_neg",
        "prompt": "A person walking in a city at night with bright lights and tall buildings. Footsteps on pavement.",
        "negative": "",
    },
]

OOM_STRESS_TESTS = [
    {"name": "oom_1080p_97f",  "width": 1920, "height": 1080, "frames": 97,  "expect_oom": True},
    {"name": "oom_720p_241f",  "width": 1280, "height": 720,  "frames": 241, "expect_oom": True},
    {"name": "oom_720p_193f",  "width": 1280, "height": 720,  "frames": 193, "expect_oom": "maybe"},
    {"name": "oom_720p_121f",  "width": 1280, "height": 720,  "frames": 121, "expect_oom": False},
    {"name": "oom_540p_193f",  "width": 960,  "height": 540,  "frames": 193, "expect_oom": False},
]

MOOD_TESTS = [
    {"mood": "humor",       "scene": "Korean couple arguing playfully about who ate the last ramyeon"},
    {"mood": "touching",    "scene": "Elderly Korean grandmother hugging her granddaughter at a train station"},
    {"mood": "horror",      "scene": "Dark empty Korean apartment hallway at night, flickering fluorescent light"},
    {"mood": "anger",       "scene": "Korean office worker slamming documents on desk in frustration"},
    {"mood": "sadness",     "scene": "Korean man sitting alone on a bench in the rain, holding an umbrella"},
    {"mood": "shock",       "scene": "Korean woman covering her mouth in disbelief while reading her phone"},
    {"mood": "daily",       "scene": "Korean family eating dinner together at home, passing side dishes"},
    {"mood": "info",        "scene": "Korean news studio with anchor presenting breaking news graphics"},
    {"mood": "controversy", "scene": "Heated Korean panel discussion on a TV talk show set"},
]

INTEGRATION_TESTS = [
    {
        "name": "full_pipeline_humor",
        "korean_post_title": "오늘 회사에서 생긴 황당한 일",
        "korean_post_body": "오늘 점심시간에 팀장님이 갑자기 회의실로 부르셔서 뭔가 했더니...",
        "mood": "humor",
    },
    {
        "name": "full_pipeline_with_image",
        "korean_post_title": "강남역 맛집 후기",
        "korean_post_body": "여기 파스타가 진짜 대박이에요 분위기도 좋고...",
        "attached_image": "test/fixtures/food_photo.jpg",
        "mood": "daily",
    },
]


# ====================================================================
# 유틸리티 함수
# ====================================================================

def _get_comfy_client():
    """ComfyUI 클라이언트 인스턴스를 반환한다."""
    from ai_worker.video.comfy_client import ComfyUIClient
    from config.settings import COMFYUI_URL, VIDEO_OUTPUT_DIR
    return ComfyUIClient(
        base_url=COMFYUI_URL,
        output_dir=Path(VIDEO_OUTPUT_DIR),
    )


def _ensure_output_dir(category: str, test_id: str) -> Path:
    """테스트 출력 디렉터리를 생성하고 반환한다."""
    out_dir = BASE_OUTPUT_DIR / category / test_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _get_vram_usage() -> dict:
    """현재 VRAM 사용량/총량(MB)을 nvidia-smi로 조회한다."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("\n")[0].split(", ")
            return {"vram_used_mb": int(parts[0]), "vram_total_mb": int(parts[1])}
    except Exception:
        pass
    return {"vram_used_mb": 0, "vram_total_mb": 0}


def _get_vram_peak_mb() -> float:
    """현재 GPU VRAM 사용량(MB)을 nvidia-smi로 조회한다."""
    return float(_get_vram_usage()["vram_used_mb"])


def _extract_thumbnail(video_path: Path, output_path: Path) -> None:
    """비디오 첫 프레임을 썸네일로 추출한다."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vframes", "1",
             "-q:v", "2", str(output_path)],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.warning("썸네일 추출 실패: %s", e)


def _save_meta(out_dir: Path, test_id: str, meta: dict) -> None:
    """메타데이터를 JSON 파일로 저장한다."""
    meta_path = out_dir / f"{test_id}_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _get_file_size(path: Path) -> int:
    """파일 크기(바이트)를 반환한다."""
    return path.stat().st_size if path.exists() else 0


async def _run_t2v_test(
    client,
    test_id: str,
    category: str,
    prompt: str,
    negative: str = NEGATIVE_PROMPT_V2,
    width: int = 1280,
    height: int = 720,
    frames: int = 97,
    steps: int = 20,
    cfg: float = 3.5,
    fps: int = 24,
    seed: int = -1,
    use_distilled: bool = False,
    timeout: int = 300,
) -> dict:
    """T2V 테스트를 실행하고 메타데이터를 반환한다."""
    out_dir = _ensure_output_dir(category, test_id)
    model_file = (
        "ltx-2-19b-distilled-fp8.safetensors" if use_distilled
        else "ltx-2-19b-dev-fp8.safetensors"
    )
    meta = {
        "test_id": test_id,
        "category": category,
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "frames": frames,
        "steps": steps,
        "cfg": cfg,
        "fps": fps,
        "seed": seed,
        "use_distilled": use_distilled,
        "model_file": model_file,
        "model_precision": "fp8",
        "comfyui_vram_mode": "lowvram",
        "workflow": "t2v_ltx2_distilled.json" if use_distilled else "t2v_ltx2.json",
    }

    start_time = time.time()
    vram_before_info = _get_vram_usage()
    vram_before = float(vram_before_info["vram_used_mb"])

    try:
        result_path = await client.generate_t2v(
            prompt=prompt,
            negative_prompt=negative,
            width=width, height=height,
            num_frames=frames,
            fps=fps,
            steps=steps,
            cfg=cfg,
            seed=seed,
            use_distilled=use_distilled,
            timeout=timeout,
        )
        elapsed = time.time() - start_time
        vram_after = _get_vram_peak_mb()

        # 결과 파일 복사
        import shutil
        video_out = out_dir / f"{test_id}.mp4"
        shutil.copy2(result_path, video_out)

        # 썸네일 추출
        thumb_out = out_dir / f"{test_id}_thumbnail.jpg"
        _extract_thumbnail(video_out, thumb_out)

        meta.update({
            "success": True,
            "generation_time_seconds": round(elapsed, 2),
            "vram_before_mb": vram_before,
            "vram_peak_mb": vram_after,
            "vram_after_mb": _get_vram_peak_mb(),
            "vram_total_mb": vram_before_info["vram_total_mb"],
            "vram_delta_mb": round(vram_after - vram_before, 1),
            "file_size_bytes": _get_file_size(video_out),
            "output_path": str(video_out),
        })
        logger.info("[%s] 성공: %.1fs, %.0fMB VRAM, %s", test_id, elapsed, vram_after, video_out.name)

    except Exception as e:
        elapsed = time.time() - start_time
        meta.update({
            "success": False,
            "generation_time_seconds": round(elapsed, 2),
            "error_message": str(e),
        })
        logger.error("[%s] 실패: %s (%.1fs)", test_id, e, elapsed)

        # OOM 후 VRAM 정리
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        gc.collect()

    _save_meta(out_dir, test_id, meta)
    return meta


async def _run_upscale_test(
    client,
    test_id: str,
    category: str,
    prompt: str,
    negative: str = NEGATIVE_PROMPT_V2,
    frames: int = 97,
    fps: int = 24,
    seed: int = -1,
    timeout: int = 600,
) -> dict:
    """2-Stage 업스케일 테스트를 실행한다."""
    out_dir = _ensure_output_dir(category, test_id)
    meta = {
        "test_id": test_id,
        "category": category,
        "prompt": prompt,
        "negative_prompt": negative,
        "frames": frames,
        "fps": fps,
        "seed": seed,
        "workflow": "t2v_ltx2_upscale.json",
    }

    start_time = time.time()
    vram_before = _get_vram_peak_mb()

    try:
        result_path = await client.generate_t2v_with_upscale(
            prompt=prompt,
            negative_prompt=negative,
            num_frames=frames,
            fps=fps,
            seed=seed,
            timeout=timeout,
        )
        elapsed = time.time() - start_time
        vram_after = _get_vram_peak_mb()

        import shutil
        video_out = out_dir / f"{test_id}.mp4"
        shutil.copy2(result_path, video_out)

        thumb_out = out_dir / f"{test_id}_thumbnail.jpg"
        _extract_thumbnail(video_out, thumb_out)

        meta.update({
            "success": True,
            "generation_time_seconds": round(elapsed, 2),
            "vram_peak_mb": vram_after,
            "file_size_bytes": _get_file_size(video_out),
            "output_path": str(video_out),
        })
        logger.info("[%s] 업스케일 성공: %.1fs, %s", test_id, elapsed, video_out.name)

    except Exception as e:
        elapsed = time.time() - start_time
        meta.update({
            "success": False,
            "generation_time_seconds": round(elapsed, 2),
            "error_message": str(e),
        })
        logger.error("[%s] 업스케일 실패: %s", test_id, e)
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        gc.collect()

    _save_meta(out_dir, test_id, meta)
    return meta


async def _run_i2v_test(
    client,
    test_id: str,
    category: str,
    prompt: str,
    image_path: Path,
    negative: str = NEGATIVE_PROMPT_V2,
    timeout: int = 300,
) -> dict:
    """I2V 테스트를 실행한다."""
    out_dir = _ensure_output_dir(category, test_id)
    meta = {
        "test_id": test_id,
        "category": category,
        "prompt": prompt,
        "image_path": str(image_path),
        "workflow": "i2v_ltx2.json",
    }

    start_time = time.time()
    try:
        result_path = await client.generate_i2v(
            prompt=prompt,
            init_image_path=image_path,
            negative_prompt=negative,
            timeout=timeout,
        )
        elapsed = time.time() - start_time

        import shutil
        video_out = out_dir / f"{test_id}.mp4"
        shutil.copy2(result_path, video_out)

        thumb_out = out_dir / f"{test_id}_thumbnail.jpg"
        _extract_thumbnail(video_out, thumb_out)

        meta.update({
            "success": True,
            "generation_time_seconds": round(elapsed, 2),
            "file_size_bytes": _get_file_size(video_out),
        })
        logger.info("[%s] I2V 성공: %.1fs", test_id, elapsed)

    except Exception as e:
        elapsed = time.time() - start_time
        meta.update({
            "success": False,
            "generation_time_seconds": round(elapsed, 2),
            "error_message": str(e),
        })
        logger.error("[%s] I2V 실패: %s", test_id, e)

    _save_meta(out_dir, test_id, meta)
    return meta


# ====================================================================
# 테스트 카테고리별 실행 함수
# ====================================================================

async def run_quality_tests(client) -> list[dict]:
    """6-1. 화질 비교 테스트 — Distilled vs Upscale vs Full."""
    logger.info("=" * 60)
    logger.info("화질 비교 테스트 시작 (9 테스트)")
    logger.info("=" * 60)
    results = []

    for i, p in enumerate(QUALITY_TEST_PROMPTS):
        # Distilled
        r = await _run_t2v_test(
            client, f"Q-D-{i+1}", "quality_comparison",
            prompt=p["prompt"], use_distilled=True, steps=8, cfg=1.0,
        )
        results.append(r)

        # Upscale
        r = await _run_upscale_test(
            client, f"Q-U-{i+1}", "quality_comparison",
            prompt=p["prompt"],
        )
        results.append(r)

        # Full
        r = await _run_t2v_test(
            client, f"Q-F-{i+1}", "quality_comparison",
            prompt=p["prompt"],
        )
        results.append(r)

    return results


async def run_audio_tests(client) -> list[dict]:
    """6-2. TTS/오디오 비교 테스트."""
    logger.info("=" * 60)
    logger.info("오디오 비교 테스트 시작 (9 테스트)")
    logger.info("=" * 60)
    results = []

    for case in AUDIO_TEST_CASES:
        name = case["name"]

        # LTX-2 내장 오디오
        r = await _run_t2v_test(
            client, f"A-LTX-{name}", "audio_comparison",
            prompt=case["video_prompt_with_audio"],
        )
        results.append(r)

        # 오디오 없는 버전 (Fish Speech용 영상)
        r = await _run_t2v_test(
            client, f"A-FISH-{name}", "audio_comparison",
            prompt=case["video_prompt_no_audio"],
        )
        results.append(r)

        # 현재 파이프라인 방식 (오디오 없는 영상 + 별도 TTS 예정)
        r = await _run_t2v_test(
            client, f"A-BOTH-{name}", "audio_comparison",
            prompt=case["video_prompt_no_audio"],
        )
        results.append(r)

    return results


async def run_i2v_tests(client) -> list[dict]:
    """6-3. I2V 품질 테스트."""
    logger.info("=" * 60)
    logger.info("I2V 비교 테스트 시작")
    logger.info("=" * 60)
    results = []

    for case in I2V_TEST_CASES:
        name = case["name"]
        image_path = Path(case["test_image"])

        if not image_path.exists():
            logger.warning("[I-I2V-%s] 테스트 이미지 없음 (%s) — skip", name, image_path)
            results.append({
                "test_id": f"I-I2V-{name}", "success": False,
                "error_message": f"테스트 이미지 없음: {image_path}",
            })
        else:
            r = await _run_i2v_test(
                client, f"I-I2V-{name}", "i2v_comparison",
                prompt=case["i2v_prompt"], image_path=image_path,
            )
            results.append(r)

        # T2V 비교 (이미지 없어도 실행)
        r = await _run_t2v_test(
            client, f"I-T2V-{name}", "i2v_comparison",
            prompt=case["t2v_prompt"],
        )
        results.append(r)

    return results


async def run_person_tests(client) -> list[dict]:
    """6-4. 인물 Tier 테스트."""
    logger.info("=" * 60)
    logger.info("인물 Tier 테스트 시작 (8 테스트)")
    logger.info("=" * 60)
    results = []

    for test in PERSON_TIER_TESTS:
        r = await _run_t2v_test(
            client, f"P-{test['name']}", "person_tier",
            prompt=test["prompt"],
        )
        r["tier"] = test["tier"]
        results.append(r)

    return results


async def run_param_sweep_tests(client) -> list[dict]:
    """6-5. 파라미터 스윕 테스트."""
    logger.info("=" * 60)
    logger.info("파라미터 스윕 테스트 시작 (%d 테스트)", len(PARAM_SWEEP_TESTS))
    logger.info("=" * 60)
    results = []
    base_prompt = QUALITY_TEST_PROMPTS[0]["prompt"]

    for test in PARAM_SWEEP_TESTS:
        r = await _run_t2v_test(
            client, f"PS-{test['name']}", "param_sweep",
            prompt=base_prompt,
            width=test["width"], height=test["height"],
            frames=test["frames"], steps=test["steps"],
            cfg=test.get("cfg", 3.5),
            fps=test.get("fps", 24),
            timeout=600,
        )
        results.append(r)

    return results


async def run_seed_tests(client) -> list[dict]:
    """6-6. 시드 일관성 테스트."""
    logger.info("=" * 60)
    logger.info("시드 일관성 테스트 시작 (4 테스트)")
    logger.info("=" * 60)
    results = []
    base_prompt = QUALITY_TEST_PROMPTS[0]["prompt"]

    for test in SEED_TESTS:
        r = await _run_t2v_test(
            client, f"SD-{test['name']}", "seed_consistency",
            prompt=base_prompt, seed=test["seed"],
        )
        results.append(r)

    return results


async def run_negative_tests(client) -> list[dict]:
    """6-7. 네거티브 프롬프트 효과 테스트."""
    logger.info("=" * 60)
    logger.info("네거티브 프롬프트 테스트 시작 (4 테스트)")
    logger.info("=" * 60)
    results = []

    for test in NEGATIVE_PROMPT_TESTS:
        r = await _run_t2v_test(
            client, f"NP-{test['name']}", "negative_prompt",
            prompt=test["prompt"], negative=test["negative"],
            seed=42,
        )
        results.append(r)

    return results


async def run_oom_tests(client) -> list[dict]:
    """6-8. OOM 스트레스 테스트."""
    logger.info("=" * 60)
    logger.info("OOM 스트레스 테스트 시작 (5 테스트)")
    logger.info("=" * 60)
    results = []
    base_prompt = QUALITY_TEST_PROMPTS[0]["prompt"]

    for test in OOM_STRESS_TESTS:
        r = await _run_t2v_test(
            client, f"OOM-{test['name']}", "oom_stress",
            prompt=base_prompt,
            width=test["width"], height=test["height"],
            frames=test["frames"],
            timeout=600,
        )
        r["expect_oom"] = test["expect_oom"]
        results.append(r)

    return results


async def run_mood_tests(client) -> list[dict]:
    """6-9. Mood별 스타일 테스트."""
    logger.info("=" * 60)
    logger.info("Mood 스타일 테스트 시작 (9 테스트)")
    logger.info("=" * 60)
    results = []

    try:
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
    except Exception as e:
        logger.error("VideoPromptEngine 로드 실패: %s — Mood 테스트 스킵", e)
        return results

    for test in MOOD_TESTS:
        mood = test["mood"]
        try:
            prompt = engine.generate_prompt(
                text_lines=[test["scene"]],
                mood=mood,
                title=f"Mood 테스트: {mood}",
            )
        except Exception as e:
            logger.error("[MOOD-%s] 프롬프트 생성 실패: %s", mood, e)
            results.append({
                "test_id": f"MOOD-{mood}", "success": False,
                "error_message": f"프롬프트 생성 실패: {e}",
            })
            continue

        r = await _run_t2v_test(
            client, f"MOOD-{mood}", "mood_styles",
            prompt=prompt,
        )
        r["mood"] = mood
        r["scene_description"] = test["scene"]
        r["generated_prompt"] = prompt
        results.append(r)

    return results


async def run_integration_tests(client) -> list[dict]:
    """6-10. 전체 파이프라인 통합 테스트."""
    logger.info("=" * 60)
    logger.info("통합 테스트 시작 (2 테스트)")
    logger.info("=" * 60)
    results = []

    try:
        from ai_worker.video.prompt_engine import VideoPromptEngine
        engine = VideoPromptEngine()
    except Exception as e:
        logger.error("VideoPromptEngine 로드 실패: %s", e)
        return results

    for test in INTEGRATION_TESTS:
        name = test["name"]
        try:
            prompt = engine.generate_prompt(
                text_lines=[test["korean_post_body"]],
                mood=test["mood"],
                title=test["korean_post_title"],
            )
        except Exception as e:
            logger.error("[INT-%s] 프롬프트 생성 실패: %s", name, e)
            results.append({
                "test_id": f"INT-{name}", "success": False,
                "error_message": f"프롬프트 생성 실패: {e}",
            })
            continue

        if "attached_image" in test:
            image_path = Path(test["attached_image"])
            if image_path.exists():
                r = await _run_i2v_test(
                    client, f"INT-{name}", "integration",
                    prompt=prompt, image_path=image_path,
                )
            else:
                logger.warning("[INT-%s] 이미지 없음 — T2V로 대체", name)
                r = await _run_t2v_test(
                    client, f"INT-{name}", "integration",
                    prompt=prompt,
                )
        else:
            r = await _run_t2v_test(
                client, f"INT-{name}", "integration",
                prompt=prompt,
            )

        r["korean_title"] = test["korean_post_title"]
        r["generated_prompt"] = prompt
        results.append(r)

    return results


# ====================================================================
# TEST_SUMMARY.md 생성
# ====================================================================

def generate_test_summary(all_results: dict[str, list[dict]]) -> None:
    """테스트 결과 요약 마크다운을 생성한다."""
    summary_path = BASE_OUTPUT_DIR / "TEST_SUMMARY.md"
    lines = [
        f"# LTX-2 테스트 결과 요약",
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for category, results in all_results.items():
        if not results:
            continue

        lines.append(f"## {category}")
        lines.append("")
        lines.append("| 테스트 ID | 워크플로우 | 생성 시간 | 파일 크기 | VRAM 피크 | 성공 |")
        lines.append("|-----------|-----------|----------|----------|----------|------|")

        for r in results:
            test_id = r.get("test_id", "?")
            wf = r.get("workflow", "-")
            gen_time = r.get("generation_time_seconds", "-")
            if isinstance(gen_time, (int, float)):
                gen_time = f"{gen_time:.1f}s"
            file_size = r.get("file_size_bytes", 0)
            if file_size > 0:
                file_size = f"{file_size / 1024 / 1024:.1f}MB"
            else:
                file_size = "-"
            vram = r.get("vram_peak_mb", 0)
            if vram > 0:
                vram = f"{vram:.0f}MB"
            else:
                vram = "-"
            success = "pass" if r.get("success") else "FAIL"
            error = r.get("error_message", "")
            if error:
                success = f"FAIL: {error[:50]}"

            lines.append(f"| {test_id} | {wf} | {gen_time} | {file_size} | {vram} | {success} |")

        lines.append("")

        # 성공/실패 요약
        total = len(results)
        passed = sum(1 for r in results if r.get("success"))
        lines.append(f"**{passed}/{total} 통과**")
        lines.append("")

    # OOM 경계값
    if "OOM 스트레스" in all_results:
        lines.append("## OOM 경계값")
        lines.append("")
        lines.append("| 해상도 | 프레임 | 예상 | 결과 |")
        lines.append("|--------|--------|------|------|")
        for r in all_results.get("OOM 스트레스", []):
            test_id = r.get("test_id", "?")
            expect = r.get("expect_oom", "?")
            actual = "OOM" if not r.get("success") else "OK"
            lines.append(f"| {test_id} | {r.get('frames', '?')} | {expect} | {actual} |")
        lines.append("")

    # 판단 필요 항목
    lines.extend([
        "## 판단 필요 항목",
        "- [ ] Distilled vs Upscale: 어느 쪽을 운영 기본으로 할 것인가?",
        "- [ ] Fish Speech vs LTX-2 오디오: 어느 쪽 품질이 나은가?",
        "- [ ] 인물 Tier 1 클로즈업: 운영에서 허용할 것인가?",
        "",
    ])

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("TEST_SUMMARY.md 생성: %s", summary_path)


# ====================================================================
# 메인 실행
# ====================================================================

async def main():
    parser = argparse.ArgumentParser(description="LTX-2 ComfyUI 종합 테스트")
    parser.add_argument("--all", action="store_true", help="전체 테스트")
    parser.add_argument("--quality", action="store_true", help="화질 비교")
    parser.add_argument("--audio", action="store_true", help="오디오 비교")
    parser.add_argument("--i2v", action="store_true", help="I2V 비교")
    parser.add_argument("--person", action="store_true", help="인물 Tier")
    parser.add_argument("--params", action="store_true", help="파라미터 스윕")
    parser.add_argument("--seed", action="store_true", help="시드 일관성")
    parser.add_argument("--negative", action="store_true", help="네거티브 프롬프트")
    parser.add_argument("--oom", action="store_true", help="OOM 스트레스")
    parser.add_argument("--mood", action="store_true", help="Mood 스타일")
    parser.add_argument("--integration", action="store_true", help="통합 테스트")
    parser.add_argument("--single", type=str, help="단일 테스트 ID (예: Q-D-1)")

    args = parser.parse_args()

    # 카테고리 선택 없으면 도움말 출력
    any_selected = any([
        args.all, args.quality, args.audio, args.i2v, args.person,
        args.params, args.seed, args.negative, args.oom, args.mood,
        args.integration, args.single,
    ])
    if not any_selected:
        parser.print_help()
        return

    BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = _get_comfy_client()

    # health check
    if not await client.health_check():
        logger.error("ComfyUI 서버에 연결할 수 없습니다. 서버를 시작하세요.")
        logger.error("  docker compose up -d comfyui")
        return

    logger.info("ComfyUI 서버 연결 확인 OK")
    check_system_requirements()

    all_results: dict[str, list[dict]] = {}

    # 단일 테스트
    if args.single:
        test_id = args.single
        base_prompt = QUALITY_TEST_PROMPTS[0]["prompt"]
        r = await _run_t2v_test(
            client, test_id, "single",
            prompt=base_prompt,
        )
        all_results["단일 테스트"] = [r]
        generate_test_summary(all_results)
        return

    if args.all or args.quality:
        all_results["화질 비교"] = await run_quality_tests(client)

    if args.all or args.person:
        all_results["인물 Tier"] = await run_person_tests(client)

    if args.all or args.params:
        all_results["파라미터 스윕"] = await run_param_sweep_tests(client)

    if args.all or args.seed:
        all_results["시드 일관성"] = await run_seed_tests(client)

    if args.all or args.negative:
        all_results["네거티브 프롬프트"] = await run_negative_tests(client)

    if args.all or args.oom:
        all_results["OOM 스트레스"] = await run_oom_tests(client)

    if args.all or args.mood:
        all_results["Mood 스타일"] = await run_mood_tests(client)

    if args.all or args.audio:
        all_results["오디오 비교"] = await run_audio_tests(client)

    if args.all or args.i2v:
        all_results["I2V 비교"] = await run_i2v_tests(client)

    if args.all or args.integration:
        all_results["통합 테스트"] = await run_integration_tests(client)

    generate_test_summary(all_results)

    # 최종 요약 출력
    total = sum(len(r) for r in all_results.values())
    passed = sum(
        sum(1 for r in results if r.get("success"))
        for results in all_results.values()
    )
    logger.info("=" * 60)
    logger.info("전체 결과: %d/%d 통과", passed, total)
    logger.info("결과 디렉터리: %s", BASE_OUTPUT_DIR)
    logger.info("요약: %s", BASE_OUTPUT_DIR / "TEST_SUMMARY.md")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
