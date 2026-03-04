"""scene_idx 기반 plan↔SceneDecision 매핑 단위 테스트

_scenes_to_plan_and_sentences()가 plan entry에 scene_idx를 올바르게 삽입하고,
_get_scene_for_entry()가 인덱스 기반 O(1) 조회를 수행하는지 검증.

Usage:
    python -m pytest test/test_scene_idx_mapping.py -v
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ai_worker.scene.director import SceneDecision
from ai_worker.renderer.layout import (
    _get_scene_for_entry,
    _scenes_to_plan_and_sentences,
)


def _make_scene(
    scene_type: str,
    text_lines: list,
    image_url: str | None = None,
    video_clip_path: str | None = None,
) -> SceneDecision:
    return SceneDecision(
        type=scene_type,
        text_lines=text_lines,
        image_url=image_url,
        video_clip_path=video_clip_path,
    )


# ──────────────────────────────────────────────────────────────────────
# _scenes_to_plan_and_sentences: scene_idx 삽입 검증
# ──────────────────────────────────────────────────────────────────────

def test_plan_entries_contain_scene_idx():
    """모든 plan entry에 scene_idx가 올바른 인덱스로 삽입되어야 한다."""
    scenes = [
        _make_scene("intro", ["후킹 텍스트"]),
        _make_scene("image_text", ["본문 텍스트 1"], image_url="http://img/1.jpg"),
        _make_scene("text_only", ["줄1", "줄2"]),
        _make_scene("image_only", ["캡션"], image_url="http://img/2.jpg"),
        _make_scene("outro", ["마무리"]),
    ]
    sentences, plan, images = _scenes_to_plan_and_sentences(scenes)

    # text_only 씬은 text_lines 개수만큼 plan entry가 생성됨
    assert len(plan) == 6  # intro(1) + image_text(1) + text_only(2) + image_only(1) + outro(1)

    # 각 entry의 scene_idx 검증
    assert plan[0]["scene_idx"] == 0  # intro → scenes[0]
    assert plan[1]["scene_idx"] == 1  # image_text → scenes[1]
    assert plan[2]["scene_idx"] == 2  # text_only 줄1 → scenes[2]
    assert plan[3]["scene_idx"] == 2  # text_only 줄2 → scenes[2] (동일 씬)
    assert plan[4]["scene_idx"] == 3  # image_only → scenes[3]
    assert plan[5]["scene_idx"] == 4  # outro → scenes[4]


def test_plan_entry_types_correct():
    """plan entry의 type 필드가 씬 타입과 일치해야 한다."""
    scenes = [
        _make_scene("intro", ["인트로"]),
        _make_scene("image_text", ["본문"], image_url="http://img/a.jpg"),
        _make_scene("outro", ["아웃트로"]),
    ]
    _, plan, _ = _scenes_to_plan_and_sentences(scenes)

    assert [e["type"] for e in plan] == ["intro", "image_text", "outro"]


# ──────────────────────────────────────────────────────────────────────
# _get_scene_for_entry: 인덱스 기반 조회 검증
# ──────────────────────────────────────────────────────────────────────

def test_get_scene_by_scene_idx():
    """scene_idx가 있으면 텍스트 매칭 없이 직접 인덱스로 조회해야 한다."""
    scenes = [
        _make_scene("intro", ["인트로"], video_clip_path="/tmp/clip0.mp4"),
        _make_scene("image_text", ["본문"], video_clip_path="/tmp/clip1.mp4"),
        _make_scene("outro", ["아웃트로"], video_clip_path="/tmp/clip2.mp4"),
    ]
    sentences = [
        {"text": "인트로"},
        {"text": "본문"},
        {"text": "아웃트로"},
    ]

    # scene_idx=1 → scenes[1] 직접 반환
    entry = {"type": "image_text", "sent_idx": 1, "scene_idx": 1}
    result = _get_scene_for_entry(entry, sentences, scenes)
    assert result is scenes[1]
    assert result.video_clip_path == "/tmp/clip1.mp4"


def test_get_scene_idx_fallback_to_text_match():
    """scene_idx가 없으면 기존 텍스트 매칭 폴백을 사용해야 한다."""
    scenes = [
        _make_scene("intro", ["인트로 텍스트"]),
        _make_scene("image_text", ["본문 텍스트 A"]),
    ]
    sentences = [
        {"text": "인트로 텍스트"},
        {"text": "본문 텍스트 A"},
    ]

    # scene_idx 없는 레거시 entry → 텍스트 매칭
    entry = {"type": "image_text", "sent_idx": 1}
    result = _get_scene_for_entry(entry, sentences, scenes)
    assert result is scenes[1]


def test_get_scene_none_when_scenes_list_none():
    """scenes_list가 None이면 None 반환."""
    entry = {"type": "intro", "sent_idx": 0, "scene_idx": 0}
    result = _get_scene_for_entry(entry, [{"text": "test"}], None)
    assert result is None


def test_get_scene_idx_out_of_bounds():
    """scene_idx가 범위 밖이면 텍스트 매칭으로 폴백."""
    scenes = [
        _make_scene("intro", ["테스트"]),
    ]
    sentences = [{"text": "테스트"}]

    entry = {"type": "intro", "sent_idx": 0, "scene_idx": 99}
    result = _get_scene_for_entry(entry, sentences, scenes)
    # 텍스트 매칭 폴백으로 scenes[0] 반환
    assert result is scenes[0]


# ──────────────────────────────────────────────────────────────────────
# 통합: plan 생성 → scene 조회 라운드트립
# ──────────────────────────────────────────────────────────────────────

def test_roundtrip_plan_to_scene_with_video_clips():
    """plan 생성 후 각 entry에서 올바른 SceneDecision(video_clip_path 포함)을 조회."""
    scenes = [
        _make_scene("intro", ["후킹"], video_clip_path="/tmp/v0.mp4"),
        _make_scene("image_text", ["본문1"], image_url="http://img/1.jpg", video_clip_path="/tmp/v1.mp4"),
        _make_scene("text_only", ["줄A", "줄B"], video_clip_path="/tmp/v2.mp4"),
        _make_scene("outro", ["마무리"], video_clip_path="/tmp/v3.mp4"),
    ]
    sentences, plan, images = _scenes_to_plan_and_sentences(scenes)

    # 모든 plan entry가 올바른 씬으로 매핑되고 video_clip_path가 보존되는지 확인
    expected_clips = ["/tmp/v0.mp4", "/tmp/v1.mp4", "/tmp/v2.mp4", "/tmp/v2.mp4", "/tmp/v3.mp4"]
    for entry, expected_clip in zip(plan, expected_clips):
        scene = _get_scene_for_entry(entry, sentences, scenes)
        assert scene is not None, f"entry {entry} 매핑 실패"
        assert scene.video_clip_path == expected_clip


def test_dict_text_lines_with_audio():
    """text_lines가 dict(TTS 사전 생성 후) 형식일 때도 scene_idx 매핑이 정상 동작."""
    scenes = [
        _make_scene(
            "image_text",
            [{"text": "안녕하세요", "audio": "/tmp/tts_0.wav"}],
            image_url="http://img/1.jpg",
            video_clip_path="/tmp/clip.mp4",
        ),
    ]
    sentences, plan, _ = _scenes_to_plan_and_sentences(scenes)

    assert len(plan) == 1
    assert plan[0]["scene_idx"] == 0

    scene = _get_scene_for_entry(plan[0], sentences, scenes)
    assert scene is scenes[0]
    assert scene.video_clip_path == "/tmp/clip.mp4"
