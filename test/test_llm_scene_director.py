"""Phase 4 LLM Scene Director 단위 테스트.

테스트 대상:
- generate_merge_candidates() 경계값
- generate_merge_candidates_with_oversized() oversized 감지
- validate_adjacency() 정상/비정상
- validate_llm_output() 8단계 검증
- _convert_to_scene_decisions() SceneDecision 변환
- _extract_json_from_response() JSON 파싱
- estimate_tts_duration() TTS 예상 시간
- Edge Case: 빈 merge_groups, oversized 씬, LLM 폴백
"""
import pytest

from ai_worker.scene.analyzer import estimate_tts_duration
from ai_worker.scene.director import (
    MergeCandidate,
    SceneDecision,
    generate_merge_candidates,
    generate_merge_candidates_with_oversized,
    validate_adjacency,
    validate_llm_output,
    _build_llm_input,
    _build_user_prompt,
    _convert_to_scene_decisions,
    _extract_json_from_response,
)


# =====================================================================
# estimate_tts_duration()
# =====================================================================

class TestEstimateTtsDuration:
    def test_empty_text(self) -> None:
        assert estimate_tts_duration("") == 0.0

    def test_whitespace_only(self) -> None:
        assert estimate_tts_duration("   ") == 0.0

    def test_short_text(self) -> None:
        # 4자 / 4.0자/초 = 1.0초
        assert estimate_tts_duration("안녕하세") == 1.0

    def test_longer_text(self) -> None:
        # 12자 / 4.0 = 3.0초
        text = "가나다라마바사아자차카타"
        assert estimate_tts_duration(text) == 3.0

    def test_rounding(self) -> None:
        # 7자 / 4.0 = 1.75 → round(1.75, 1) = 1.8
        text = "가나다라마바사"
        assert estimate_tts_duration(text) == 1.8


# =====================================================================
# generate_merge_candidates()
# =====================================================================

class TestGenerateMergeCandidates:
    @staticmethod
    def _make_scenes(durations: list[float]) -> list[dict]:
        return [
            {"index": i, "text": f"씬{i}", "estimated_tts_sec": d, "block_type": "body"}
            for i, d in enumerate(durations)
        ]

    def test_basic_merge(self) -> None:
        """두 씬 합산이 4~6초 범위에 들어가면 후보 생성."""
        scenes = self._make_scenes([2.0, 2.5])
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        assert len(result) == 1
        assert result[0].scene_indices == [0, 1]
        assert result[0].total_duration_sec == 4.5

    def test_no_candidates_below_min(self) -> None:
        """합산이 최소 미만이면 후보 없음."""
        scenes = self._make_scenes([1.0, 1.0])
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        assert len(result) == 0

    def test_no_candidates_above_max(self) -> None:
        """첫 씬부터 최대 초과이면 해당 시작점에서 후보 없음."""
        scenes = self._make_scenes([7.0, 1.0])
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        # 7.0 단독은 > 6.0이므로 [0]으로 시작하는 후보 없음
        # [1] 단독 1.0 < 4.0 → 후보 없음
        assert len(result) == 0

    def test_multiple_overlapping_groups(self) -> None:
        """인접 슬라이딩 윈도우로 겹치는 그룹 생성."""
        scenes = self._make_scenes([1.5, 2.0, 2.5, 1.0])
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        # 가능한 조합: [0,1,2]=6.0, [1,2]=4.5, [1,2,3]=5.5, [2,3]=3.5(미달)
        ids = {tuple(r.scene_indices) for r in result}
        assert (0, 1, 2) in ids
        assert (1, 2) in ids
        assert (1, 2, 3) in ids

    def test_max_group_size_limit(self) -> None:
        """max_group_size 제한."""
        scenes = self._make_scenes([1.0] * 10)
        result = generate_merge_candidates(scenes, 4.0, 6.0, max_group_size=3)
        for r in result:
            assert r.scene_count <= 3

    def test_comment_detection(self) -> None:
        """댓글 포함 그룹 감지."""
        scenes = [
            {"index": 0, "text": "본문", "estimated_tts_sec": 2.0, "block_type": "body"},
            {"index": 1, "text": "댓글", "estimated_tts_sec": 2.5, "block_type": "comment"},
        ]
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        assert len(result) == 1
        assert result[0].contains_comment is True

    def test_empty_scenes(self) -> None:
        """빈 씬 목록."""
        assert generate_merge_candidates([], 4.0, 6.0, 5) == []

    def test_single_scene_in_range(self) -> None:
        """단일 씬이 4~6초 범위."""
        scenes = self._make_scenes([5.0])
        result = generate_merge_candidates(scenes, 4.0, 6.0, 5)
        assert len(result) == 1
        assert result[0].scene_indices == [0]


# =====================================================================
# generate_merge_candidates_with_oversized()
# =====================================================================

class TestMergeCandidatesWithOversized:
    def test_oversized_detection(self) -> None:
        scenes = [
            {"index": 0, "text": "짧은", "estimated_tts_sec": 2.0, "block_type": "body"},
            {"index": 1, "text": "긴긴긴", "estimated_tts_sec": 8.0, "block_type": "body"},
            {"index": 2, "text": "중간", "estimated_tts_sec": 3.0, "block_type": "body"},
        ]
        normal, oversized = generate_merge_candidates_with_oversized(scenes, 4.0, 6.0, 5)
        assert 1 in oversized
        assert 0 not in oversized
        # 씬1(8.0)이 시작점이면 바로 break, 씬2(3.0)와도 11.0 > 6.0
        # 씬0(2.0) + 씬1(8.0) = 10.0 > 6.0 → 씬0 시작 시 [0]만 가능 (2.0 < 4.0)

    def test_no_oversized(self) -> None:
        scenes = [
            {"index": 0, "text": "a", "estimated_tts_sec": 2.0, "block_type": "body"},
            {"index": 1, "text": "b", "estimated_tts_sec": 3.0, "block_type": "body"},
        ]
        _, oversized = generate_merge_candidates_with_oversized(scenes, 4.0, 6.0, 5)
        assert oversized == []


# =====================================================================
# validate_adjacency()
# =====================================================================

class TestValidateAdjacency:
    def test_single_element(self) -> None:
        assert validate_adjacency([5]) is True

    def test_empty(self) -> None:
        assert validate_adjacency([]) is True

    def test_continuous(self) -> None:
        assert validate_adjacency([3, 4, 5]) is True

    def test_non_continuous(self) -> None:
        assert validate_adjacency([0, 2]) is False

    def test_unordered_continuous(self) -> None:
        assert validate_adjacency([5, 3, 4]) is True

    def test_gap_in_middle(self) -> None:
        assert validate_adjacency([1, 2, 4, 5]) is False


# =====================================================================
# validate_llm_output()
# =====================================================================

class TestValidateLlmOutput:
    @staticmethod
    def _make_groups() -> list[MergeCandidate]:
        return [
            MergeCandidate("G0", 0, 1, [0, 1], 2, 4.5, False, "preview"),
            MergeCandidate("G1", 0, 2, [0, 1, 2], 3, 5.8, False, "preview"),
            MergeCandidate("G2", 3, 4, [3, 4], 2, 4.2, False, "preview"),
        ]

    def test_valid_output(self) -> None:
        groups = self._make_groups()
        llm_output = {
            "video_clips": [
                {"type": "ttv", "group_id": "G0"},
                {"type": "ttv", "group_id": "G2"},
            ],
            "static_scenes": [
                {"type": "text_only", "scene_indices": [2]},
            ],
        }
        result = validate_llm_output(llm_output, groups, [], 5, 5, [])
        assert len(result["video_clips"]) == 2

    def test_invalid_group_id(self) -> None:
        """존재하지 않는 group_id는 제거."""
        groups = self._make_groups()
        llm_output = {
            "video_clips": [
                {"type": "ttv", "group_id": "G999"},
            ],
            "static_scenes": [],
        }
        result = validate_llm_output(llm_output, groups, [], 5, 5, [])
        assert len(result["video_clips"]) == 0

    def test_scene_overlap_removal(self) -> None:
        """씬 중복 시 두 번째 제거."""
        groups = self._make_groups()
        # G0=[0,1]과 G1=[0,1,2]는 씬 0,1이 중복
        llm_output = {
            "video_clips": [
                {"type": "ttv", "group_id": "G0"},
                {"type": "ttv", "group_id": "G1"},
            ],
            "static_scenes": [],
        }
        result = validate_llm_output(llm_output, groups, [], 5, 5, [])
        assert len(result["video_clips"]) == 1
        assert result["video_clips"][0]["group_id"] == "G0"

    def test_missing_scenes_added(self) -> None:
        """누락 씬 자동 추가."""
        groups = self._make_groups()
        llm_output = {
            "video_clips": [{"type": "ttv", "group_id": "G0"}],
            "static_scenes": [],
            # 씬 2, 3, 4가 누락
        }
        result = validate_llm_output(llm_output, groups, [], 5, 5, [])
        # 누락 씬이 static_scenes에 추가되어야 함
        all_static_indices = set()
        for entry in result["static_scenes"]:
            all_static_indices.update(entry["scene_indices"])
        assert {2, 3, 4}.issubset(all_static_indices)

    def test_video_limit_exceeded(self) -> None:
        """비디오 상한 초과 시 잘림."""
        groups = [
            MergeCandidate(f"G{i}", i, i, [i], 1, 4.0, False, "p")
            for i in range(10)
        ]
        llm_output = {
            "video_clips": [
                {"type": "ttv", "group_id": f"G{i}"} for i in range(10)
            ],
            "static_scenes": [],
        }
        result = validate_llm_output(llm_output, groups, [], 10, 3, [])
        assert len(result["video_clips"]) == 3

    def test_invalid_itv_image_converts_to_ttv(self) -> None:
        """무효한 ITV image_id → TTV 전환."""
        groups = self._make_groups()
        llm_output = {
            "video_clips": [
                {"type": "itv", "group_id": "G0", "image_id": 99},
            ],
            "static_scenes": [],
        }
        itv_candidates = [{"index": 0, "original_url": "url", "local_path": "p", "suitability_score": 0.8, "category": "photo"}]
        result = validate_llm_output(llm_output, groups, itv_candidates, 5, 5, [])
        assert result["video_clips"][0]["type"] == "ttv"

    def test_non_adjacent_static_split(self) -> None:
        """비연속 인덱스 분리."""
        groups = self._make_groups()
        llm_output = {
            "video_clips": [],
            "static_scenes": [
                {"type": "text_only", "scene_indices": [0, 2, 3]},
            ],
        }
        result = validate_llm_output(llm_output, groups, [], 5, 5, [])
        # [0]과 [2,3]으로 분리되어야 함
        all_entries = result["static_scenes"]
        assert len(all_entries) >= 2

    def test_oversized_scene(self) -> None:
        """oversized 씬 직접 인덱스 지정."""
        groups = self._make_groups()
        llm_output = {
            "video_clips": [
                {"type": "ttv", "scene_index": 5},
            ],
            "static_scenes": [],
        }
        result = validate_llm_output(llm_output, groups, [], 6, 5, [5])
        assert len(result["video_clips"]) == 1


# =====================================================================
# _extract_json_from_response()
# =====================================================================

class TestExtractJson:
    def test_bare_json(self) -> None:
        raw = '{"video_clips": [], "static_scenes": []}'
        result = _extract_json_from_response(raw)
        assert result is not None
        assert result["video_clips"] == []

    def test_code_block_json(self) -> None:
        raw = '```json\n{"video_clips": [{"type": "ttv"}], "static_scenes": []}\n```'
        result = _extract_json_from_response(raw)
        assert result is not None
        assert len(result["video_clips"]) == 1

    def test_extra_text_around_json(self) -> None:
        raw = '다음과 같이 구성합니다:\n{"video_clips": [], "static_scenes": []}\n이상입니다.'
        result = _extract_json_from_response(raw)
        assert result is not None

    def test_invalid_json(self) -> None:
        raw = "이것은 JSON이 아닙니다"
        result = _extract_json_from_response(raw)
        assert result is None


# =====================================================================
# _build_llm_input() + _build_user_prompt()
# =====================================================================

class TestBuildLlmInput:
    def test_basic_structure(self) -> None:
        body_items = [
            ("안녕하세요", None, "body", None, None),
            ("반갑습니다", None, "body", None, None),
        ]
        groups = [MergeCandidate("G0", 0, 1, [0, 1], 2, 4.0, False, "안녕하세요 반갑습니다")]
        llm_input = _build_llm_input(body_items, groups, [], [], 5)

        assert llm_input["total_scenes"] == 2
        assert len(llm_input["scenes"]) == 2
        assert len(llm_input["merge_groups"]) == 1
        assert llm_input["constraints"]["max_video_clips"] == 5

    def test_user_prompt_format(self) -> None:
        body_items = [("텍스트", None, "body", None, None)]
        groups = [MergeCandidate("G0", 0, 0, [0], 1, 4.0, False, "텍스트")]
        llm_input = _build_llm_input(body_items, groups, [], [], 5)
        prompt = _build_user_prompt(llm_input)
        assert "씬 목록" in prompt
        assert "병합 가능 그룹 후보" in prompt
        assert "제약 조건" in prompt


# =====================================================================
# _convert_to_scene_decisions()
# =====================================================================

class TestConvertToSceneDecisions:
    def test_video_clip_conversion(self) -> None:
        body_items = [
            ("첫 번째 대사", None, "body", None, None),
            ("두 번째 대사", None, "body", None, None),
            ("세 번째 대사", None, "body", None, None),
        ]
        groups = [MergeCandidate("G0", 0, 1, [0, 1], 2, 4.5, False, "preview")]
        validated = {
            "video_clips": [{"type": "ttv", "group_id": "G0"}],
            "static_scenes": [{"type": "text_only", "scene_indices": [2]}],
        }

        scenes = _convert_to_scene_decisions(
            validated, body_items, groups, [], [], [],
        )
        assert len(scenes) == 2  # 1 video (merged [0,1]) + 1 static [2]
        assert scenes[0].type == "video_text"
        assert scenes[0].video_subtype == "ttv"
        assert scenes[0].video_mode == "t2v"
        assert scenes[0].merged_scene_indices == [0, 1]
        assert len(scenes[0].text_lines) == 2
        assert scenes[1].type == "text_only"
        assert scenes[1].video_mode == "static"

    def test_itv_clip_with_image(self) -> None:
        body_items = [
            ("대사", None, "body", None, None),
            ("대사2", None, "body", None, None),
        ]
        groups = [MergeCandidate("G0", 0, 1, [0, 1], 2, 5.0, False, "preview")]
        itv = [{"index": 0, "original_url": "http://img.jpg", "local_path": "/tmp/img.jpg", "suitability_score": 0.85, "category": "photo"}]
        validated = {
            "video_clips": [{"type": "itv", "group_id": "G0", "image_id": 0}],
            "static_scenes": [],
        }

        scenes = _convert_to_scene_decisions(
            validated, body_items, groups, itv, ["http://img.jpg"], [],
        )
        assert scenes[0].type == "video_text"
        assert scenes[0].video_subtype == "itv"
        assert scenes[0].video_mode == "i2v"
        assert scenes[0].image_url == "http://img.jpg"
        assert scenes[0].video_init_image == "/tmp/img.jpg"

    def test_all_static(self) -> None:
        body_items = [
            ("a", None, "body", None, None),
            ("b", None, "body", None, None),
        ]
        validated = {
            "video_clips": [],
            "static_scenes": [{"type": "text_only", "scene_indices": [0, 1]}],
        }

        scenes = _convert_to_scene_decisions(
            validated, body_items, [], [], [], [],
        )
        assert len(scenes) == 2
        assert all(s.video_mode == "static" for s in scenes)

    def test_oversized_scene(self) -> None:
        body_items = [
            ("아주 긴 대사입니다 이것은 정말 길어요 너무 길어서 6초를 초과", None, "body", None, None),
        ]
        validated = {
            "video_clips": [{"type": "ttv", "scene_index": 0}],
            "static_scenes": [],
        }
        scenes = _convert_to_scene_decisions(
            validated, body_items, [], [], [], [0],
        )
        assert len(scenes) == 1
        assert scenes[0].type == "video_text"
        assert scenes[0].video_subtype == "ttv"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
