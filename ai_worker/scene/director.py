"""Phase 4: 씬 배분 알고리즘 (Scene Director)

scene_policy.json 정책과 mood 프리셋을 기반으로
intro / body(video_text|image_text|text_only|image_only) / outro 씬을 순서대로 배분한다.

씬 흐름:
    intro(hook) → [video_text | image_text | text_only | image_only] × N → outro(closer + mood 아웃트로 이미지)

Phase 4 내부 3단계 파이프라인 (LLM 모드):
    Phase 4-A: 사전 계산 (TTS 예상, ITV 필터링, 병합 그룹 생성)
    Phase 4-B: LLM 디렉팅 (최적 조합 선택)
    Phase 4-C: 후처리 검증 (8단계 물리적 검증 + SceneDecision 변환)

씬 유형 (video_text 추가):
- video_text(itv): 이미지 기반 비디오 + 자막 (I2V)
- video_text(ttv): 텍스트 기반 비디오 + 자막 (T2V)
- image_text: 정적 이미지 + 자막
- text_only: 자막만 표시

video_mode 값:
- "t2v": ComfyUI Text-to-Video (Phase 6/7 처리)
- "i2v": ComfyUI Image-to-Video (Phase 6/7 처리)
- "static": 정적 프레임 (Phase 6/7 스킵)

- intro/outro 이미지: policy의 mood 폴더에서 랜덤 선택
"""
import json as _json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ai_worker.scene.analyzer import ResourceProfile, estimate_tts_duration
from config.settings import EMOTION_TAGS, get_domain_setting

logger = logging.getLogger(__name__)

SceneType = Literal["intro", "video_text", "image_text", "text_only", "image_only", "outro"]

# 반전/충격 키워드가 등장하면 단독 강조 처리
_HIGHLIGHT_KEYWORDS = ["반전", "충격", "결과", "결론", "사실", "진짜", "알고보니"]

# 전략별 text_only 기본 스택 크기
_STACK_BY_STRATEGY: dict[str, int] = {
    "image_heavy": 1,
    "balanced":  2,
    "text_heavy": 3,
}


@dataclass
class SceneDecision:
    type: SceneType
    text_lines: list          # str 또는 {"text": str, "audio": str|None} — TTS 사전 생성 후 dict로 교체
    image_url: str | None     # image_text / outro 에서 사용
    text_only_stack: int = 1  # text_only 씬의 실제 스택 줄 수
    emotion_tag: str = ""     # Fish Speech 감정 태그 (EMOTION_TAGS에서 자동 할당)
    voice_override: str | None = None  # 댓글 씬: comment_voices에서 random 선택
    mood: str = "daily"           # 콘텐츠 mood 키
    tts_emotion: str = ""         # TTS 감정 톤 키 (예: "gentle", "cheerful")
    bgm_path: str | None = None   # BGM 파일 경로 (intro 씬에만 설정)
    block_type: str = "body"      # "body" 또는 "comment" (렌더링 UI 분기)
    author: str | None = None     # comment 타입의 작성자 닉네임
    pre_split_lines: list[str] | None = None  # 편집실에서 나눈 원본 줄바꿈 (렌더링용)
    # --- LTX-Video 관련 필드 ---
    video_clip_path: str | None = None      # 생성된 비디오 클립 파일 경로
    video_prompt: str | None = None         # LTX-Video용 영어 프롬프트
    video_mode: str | None = None           # "t2v" | "i2v" | None
    video_init_image: str | None = None     # I2V 모드 초기 프레임 이미지 경로
    video_generation_failed: bool = False   # 비디오 생성 최종 실패 여부
    # --- Phase 4 LLM Scene Director 필드 ---
    estimated_tts_sec: float = 0.0           # TTS 예상 시간 (초) — 비디오 생성 시 목표 길이
    video_subtype: str | None = None         # "itv" | "ttv" | None (video_text에서만 사용)
    merged_scene_indices: list[int] | None = None  # 병합된 원본 씬 인덱스들


@dataclass
class MergeCandidate:
    """병합 가능한 인접 씬 그룹 후보."""
    group_id: str              # "G0", "G1", ... (LLM 참조용 ID)
    start_index: int           # 시작 씬 인덱스
    end_index: int             # 끝 씬 인덱스 (포함)
    scene_indices: list[int] = field(default_factory=list)
    scene_count: int = 0
    total_duration_sec: float = 0.0  # TTS 예상 합산 시간
    contains_comment: bool = False
    texts_preview: str = ""


_dc_session: "requests.Session | None" = None


def _get_dc_session() -> "requests.Session":
    """DCInside 이미지 다운로드용 세션 (쿠키 워밍업 포함)."""
    import requests as _req

    global _dc_session
    if _dc_session is not None:
        return _dc_session
    _dc_session = _req.Session()
    _dc_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    try:
        _dc_session.get("https://www.dcinside.com/", timeout=10)
        logger.debug("scene DC 세션 워밍업 OK (cookies=%d)",
                     len(_dc_session.cookies))
    except Exception:
        logger.debug("scene DC 세션 워밍업 실패 — 쿠키 없이 시도")
    return _dc_session


def _is_dc_url(url: str) -> bool:
    """DCInside CDN URL 여부 확인."""
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname or ""
    return any(hostname.endswith(d) for d in ("dcinside.com", "dcinside.co.kr"))


def _download_and_cache_image(url: str, cache_dir: Path) -> Path | None:
    """URL에서 이미지를 다운로드하여 캐시 디렉터리에 저장. 실패 시 None."""
    import hashlib
    import time
    import requests
    from urllib.parse import urlparse

    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    cache_path = cache_dir / f"vid_img_{url_hash}.jpg"

    if cache_path.exists() and cache_path.stat().st_size > 200:
        return cache_path

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            if _is_dc_url(url):
                sess = _get_dc_session()
                resp = sess.get(url, timeout=15, headers={
                    "Referer": "https://gall.dcinside.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Sec-Fetch-Dest": "image",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "cross-site",
                })
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36",
                    "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
                    "Accept": "image/*,*/*;q=0.8",
                }
                resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()

            if len(resp.content) < 200:
                logger.warning("[scene] 이미지 크기 의심 (%d bytes): %s", len(resp.content), url)
                return None

            cache_path.write_bytes(resp.content)
            return cache_path

        except requests.RequestException as e:
            if attempt < max_retries:
                time.sleep(1 * (attempt + 1))
                logger.debug("[scene] 이미지 다운로드 재시도 (%d/%d): %s", attempt + 1, max_retries, url)
            else:
                logger.warning("[scene] 이미지 다운로드 실패 (재시도 %d회 후): %s — %s", max_retries, url, e)
    return None


# =====================================================================
# Phase 4-A: 사전 계산 (TTS 예상, ITV 필터링, 병합 그룹 생성)
# =====================================================================

def generate_merge_candidates(
    scenes: list[dict],
    min_duration: float = 3.5,
    max_duration: float = 5.0,
    max_group_size: int = 5,
) -> list[MergeCandidate]:
    """3.5~5.0초 범위를 만족하는 모든 인접 씬 그룹 후보를 생성한다.

    댓글 전용 그룹(모든 씬이 comment)은 비디오 후보에서 제외한다.

    Args:
        scenes: [{"index": int, "text": str, "estimated_tts_sec": float, "block_type": str}]
        min_duration: 클립 최소 길이 (초)
        max_duration: 클립 최대 길이 (초)
        max_group_size: 한 그룹에 포함될 수 있는 최대 씬 수

    Returns:
        MergeCandidate 리스트 (group_id 순서)
    """
    candidates: list[MergeCandidate] = []
    n = len(scenes)
    gid = 0

    for start in range(n):
        cumulative = 0.0
        all_comment = True  # 그룹 내 모든 씬이 댓글인지 추적
        for end in range(start, min(start + max_group_size, n)):
            cumulative += scenes[end]["estimated_tts_sec"]
            cumulative_rounded = round(cumulative, 1)

            if scenes[end]["block_type"] != "comment":
                all_comment = False

            if cumulative_rounded > max_duration:
                break  # 이후 더 길어지므로 중단

            if cumulative_rounded >= min_duration:
                # 모든 씬이 댓글이면 비디오 후보에서 제외
                if all_comment:
                    continue

                scene_indices = list(range(start, end + 1))
                texts = " ".join(scenes[i]["text"] for i in scene_indices)
                contains_comment = any(
                    scenes[i]["block_type"] == "comment" for i in scene_indices
                )
                candidates.append(MergeCandidate(
                    group_id=f"G{gid}",
                    start_index=start,
                    end_index=end,
                    scene_indices=scene_indices,
                    scene_count=len(scene_indices),
                    total_duration_sec=cumulative_rounded,
                    contains_comment=contains_comment,
                    texts_preview=texts[:30] + ("..." if len(texts) > 30 else ""),
                ))
                gid += 1

    return candidates


def generate_merge_candidates_with_oversized(
    scenes: list[dict],
    min_duration: float = 3.5,
    max_duration: float = 5.0,
    max_group_size: int = 5,
) -> tuple[list[MergeCandidate], list[int]]:
    """병합 후보를 생성하되, 5초 초과 단일 씬도 별도로 반환한다.

    Returns:
        (normal_candidates, oversized_scene_indices)
    """
    normal = generate_merge_candidates(scenes, min_duration, max_duration, max_group_size)
    oversized = [s["index"] for s in scenes if s["estimated_tts_sec"] > max_duration]
    return normal, oversized


def filter_itv_candidates(
    images: list[str],
    cache_dir: Path,
    threshold: float = 0.6,
) -> list[dict]:
    """이미지 목록에서 ITV 적합 후보를 점수 내림차순으로 반환한다."""
    from ai_worker.video.image_filter import evaluate_image

    candidates: list[dict] = []
    for i, url in enumerate(images):
        local_path = _download_and_cache_image(url, cache_dir)
        if local_path is None:
            continue
        suit = evaluate_image(local_path)
        if suit.score >= threshold and "extreme_aspect_ratio" not in suit.reason:
            candidates.append({
                "index": i,
                "original_url": url,
                "local_path": str(local_path),
                "suitability_score": suit.score,
                "category": suit.category,
            })
    # 점수 내림차순 정렬 — 가장 적합한 이미지부터 사용
    candidates.sort(key=lambda x: x["suitability_score"], reverse=True)
    return candidates


def _build_llm_input(
    body_items: list[tuple[str, str | None, str, str | None, list[str] | None]],
    merge_groups: list[MergeCandidate],
    oversized_indices: list[int],
    itv_candidates: list[dict],
    max_video_clips: int,
) -> dict:
    """Phase 4-B LLM에 전달할 입력 데이터를 구성한다."""
    scenes_data: list[dict] = []
    for i, (text, _voice, bt, _author, _psl) in enumerate(body_items):
        scenes_data.append({
            "index": i,
            "text": text[:25] + ("..." if len(text) > 25 else ""),
            "tts_sec": estimate_tts_duration(text),
            "type": bt,
        })

    itv_available = len(itv_candidates)
    ttv_available = max(0, max_video_clips - itv_available)

    return {
        "total_scenes": len(body_items),
        "scenes": scenes_data,
        "merge_groups": [
            {
                "id": mg.group_id,
                "scenes": mg.scene_indices,
                "sec": mg.total_duration_sec,
                "preview": mg.texts_preview,
                "comment": mg.contains_comment,
            }
            for mg in merge_groups
        ],
        "oversized_scenes": oversized_indices,
        "itv_images": [
            {
                "img_id": c["index"],
                "score": c["suitability_score"],
                "category": c["category"],
            }
            for c in itv_candidates
        ],
        "constraints": {
            "max_video_clips": max_video_clips,
            "itv_available": itv_available,
            "ttv_available": ttv_available,
        },
    }


# =====================================================================
# Phase 4-B: LLM 디렉팅 (프롬프트 생성 + Ollama 호출)
# =====================================================================

_SCENE_DIRECTOR_SYSTEM_PROMPT = """\
당신은 유튜브 쇼츠 영상 편집자입니다.
편집실에서 확정된 씬 목록과, 파이썬이 미리 계산한 "병합 가능 그룹 후보"를 받아
각 씬을 비디오 클립 또는 정적 씬으로 어떻게 구성할지 결정합니다.

## 핵심 원칙
- 당신은 숫자 계산을 하지 않습니다. 모든 시간 합산은 이미 계산되어 제공됩니다.
- 당신의 역할은 제공된 그룹 후보(merge_groups) 중에서 최적의 조합을 "선택"하는 것입니다.

## 규칙

1. 비디오 클립은 반드시 merge_groups에서 제공된 그룹 ID(G0, G1, ...)로 지정해야 합니다.
   merge_groups에 없는 씬 조합을 직접 만들어내면 안 됩니다.
2. 선택한 그룹끼리 씬이 겹쳐서는 안 됩니다 (예: G1=[0,1,2]와 G2=[1,2]는 씬 1,2가 중복이므로 동시 선택 불가).
3. 비디오 클립(itv + ttv)의 총 개수는 {max_video_clips}개를 초과할 수 없습니다.
4. ITV 클립 수는 {itv_available}개 이하여야 합니다. 이미지는 suitability_score가 높은 순서대로 배정합니다.
5. 비디오 클립으로 선택되지 않은 씬은 text_only로 배분합니다.
   (이미지가 남아있으면 image_text도 사용 가능)
6. 비디오 클립과 정적 씬을 자연스럽게 섞어 배치합니다 (비디오만 연속 3개 이상 금지).
7. 감정 전환이 큰 지점 (반전, 충격, 댓글 시작 등)에서 비디오 클립을 배치하면 효과적입니다.
8. 댓글 씬(type="comment")은 비디오 클립에 절대 포함하지 마세요.
   댓글은 빠르게 여러 개를 보여주는 것이 효과적이므로 반드시 text_only로 배정합니다.
   merge_groups 중 contains_comment=true인 그룹은 비디오로 선택하지 마세요.
9. oversized_scenes에 표시된 씬은 단독으로 5초를 초과하는 씬입니다.
   이 씬을 비디오로 만들 경우, 해당 씬 인덱스만 단독으로 지정합니다 (그룹 ID 대신 직접 지정).

## 출력 형식 (JSON만 출력)
{{
  "video_clips": [
    {{
      "type": "itv",
      "group_id": "G3",
      "image_id": 0,
      "reason": "오프닝 후킹 구간, 시선 집중용"
    }},
    {{
      "type": "ttv",
      "group_id": "G12",
      "reason": "반전 구간, 감정 고조에 비디오 효과 극대화"
    }}
  ],
  "static_scenes": [
    {{
      "type": "text_only",
      "scene_indices": [3, 4, 5],
      "reason": "설명 구간, 정적 표시로 충분"
    }},
    {{
      "type": "image_text",
      "scene_indices": [8],
      "image_id": 1,
      "reason": "보조 이미지 활용"
    }}
  ]
}}

## 중요
- video_clips의 group_id는 반드시 merge_groups 목록에 존재하는 ID여야 합니다.
- static_scenes의 scene_indices 배열은 비디오 클립에 포함되지 않은 나머지 씬을 모두 포함해야 합니다.
- 모든 씬(0 ~ {total_scenes_minus_1})이 video_clips 또는 static_scenes 중 정확히 하나에 포함되어야 합니다.
"""


def _build_user_prompt(llm_input: dict) -> str:
    """Phase 4-B: LLM User Prompt를 구성한다."""
    total = llm_input["total_scenes"]
    constraints = llm_input["constraints"]

    # 씬 목록 테이블
    lines = [f"## 씬 목록 ({total}개)\n"]
    lines.append("| 번호 | 대사 (앞 25자) | TTS(초) | 타입 |")
    lines.append("|------|---------------|--------|------|")
    for s in llm_input["scenes"]:
        lines.append(f'| {s["index"]} | "{s["text"]}" | {s["tts_sec"]} | {s["type"]} |')

    # 병합 가능 그룹 후보 테이블
    lines.append(f"\n## 병합 가능 그룹 후보 (파이썬 사전 계산 완료)")
    if llm_input["merge_groups"]:
        lines.append("| 그룹ID | 씬 번호 | 합산 시간(초) | 댓글 포함 |")
        lines.append("|--------|---------|-------------|----------|")
        for mg in llm_input["merge_groups"]:
            comment_mark = "✓" if mg["comment"] else "✗"
            lines.append(f'| {mg["id"]} | {mg["scenes"]} | {mg["sec"]} | {comment_mark} |')
        lines.append("\n※ 위 합산 시간은 정확하게 계산된 값입니다. 직접 더하지 마세요.")
    else:
        lines.append("(후보 없음 — 모든 씬을 정적으로 배분하세요)")

    # 5초 초과 단일 씬
    lines.append("\n## 5초 초과 단일 씬")
    if llm_input["oversized_scenes"]:
        lines.append(f'인덱스: {llm_input["oversized_scenes"]}')
    else:
        lines.append("(없음)")

    # ITV 이미지
    itv_imgs = llm_input["itv_images"]
    lines.append(f"\n## ITV 사용 가능 이미지 ({len(itv_imgs)}개)")
    if itv_imgs:
        lines.append("| 이미지ID | 적합도 | 카테고리 |")
        lines.append("|----------|--------|---------|")
        for img in itv_imgs:
            lines.append(f'| {img["img_id"]} | {img["score"]} | {img["category"]} |')
    else:
        lines.append("(ITV 적합 이미지 없음)")

    # 제약 조건
    lines.append("\n## 제약 조건")
    lines.append(f'- 비디오 클립(itv + ttv) 최대: {constraints["max_video_clips"]}개')
    lines.append(f'- ITV 최대: {constraints["itv_available"]}개')
    lines.append(f'- TTV 최대: {constraints["ttv_available"]}개')
    lines.append("\n위 정보를 바탕으로, 비디오 클립 그룹과 정적 씬을 자연스럽게 구성해주세요.")

    return "\n".join(lines)


def _extract_json_from_response(raw: str) -> dict | None:
    """LLM 응답에서 JSON 객체를 추출한다."""
    # 1차: 코드 블록에서 추출
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(1))
        except _json.JSONDecodeError:
            pass

    # 2차: 전체 텍스트에서 JSON 객체 추출
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except _json.JSONDecodeError:
            pass

    return None


def _call_scene_director_llm(
    llm_input: dict,
    timeout: int = 60,
    post_id: int | None = None,
) -> dict | None:
    """Phase 4-B: LLM을 호출하여 씬 디렉팅 결과를 반환한다.

    Returns:
        파싱된 LLM 출력 dict 또는 실패 시 None
    """
    from ai_worker.script.client import call_ollama_raw

    constraints = llm_input["constraints"]
    system_prompt = _SCENE_DIRECTOR_SYSTEM_PROMPT.format(
        max_video_clips=constraints["max_video_clips"],
        itv_available=constraints["itv_available"],
        total_scenes_minus_1=llm_input["total_scenes"] - 1,
    )
    user_prompt = _build_user_prompt(llm_input)
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    from ai_worker.script.logger import LLMCallTimer, log_llm_call

    raw = ""
    success = True
    error_msg: str | None = None
    result: dict | None = None

    try:
        with LLMCallTimer() as timer:
            raw = call_ollama_raw(
                prompt=full_prompt,
                max_tokens=2048,
                timeout=timeout,
            )
    except Exception as exc:
        success = False
        error_msg = str(exc)
        logger.error("[scene_director] LLM 호출 실패: %s", exc)

    # JSON 파싱 (성공 시에만)
    parsed_for_log: dict | None = None
    if raw and success:
        result = _extract_json_from_response(raw)
        if result is not None:
            video_clips = result.get("video_clips", [])
            static_scenes = result.get("static_scenes", [])
            parsed_for_log = {
                "total_scenes": llm_input["total_scenes"],
                "video_clip_count": len(video_clips),
                "itv_count": sum(1 for c in video_clips if c.get("type") == "itv"),
                "ttv_count": sum(1 for c in video_clips if c.get("type") == "ttv"),
                "text_only_count": sum(
                    1 for s in static_scenes if s.get("type") == "text_only"
                ),
                "video_clips": video_clips,
                "static_scenes": static_scenes,
                "merge_groups_used": [c.get("group_id") for c in video_clips],
                "merge_groups_map": {
                    mg["id"]: mg["scenes"]
                    for mg in llm_input.get("merge_groups", [])
                },
                "scenes_input": [
                    {"index": s["index"], "text": s["text"], "tts_sec": s["tts_sec"], "type": s["type"]}
                    for s in llm_input.get("scenes", [])
                ],
                "oversized_scenes": llm_input.get("oversized_scenes", []),
            }
        else:
            logger.error("[scene_director] LLM 응답 JSON 파싱 실패: %s", raw[:500])

    # 항상 로깅
    log_llm_call(
        call_type="scene_director",
        post_id=post_id,
        model_name=None,
        prompt_text=full_prompt,
        raw_response=raw,
        parsed_result=parsed_for_log,
        image_count=len(llm_input.get("itv_images", [])),
        content_length=len(user_prompt),
        success=success and result is not None,
        error_message=error_msg,
        duration_ms=timer.elapsed_ms,
    )

    if raw and success:
        logger.info(
            "[scene_director] LLM 응답 수신: %d자 (%dms)",
            len(raw), timer.elapsed_ms,
        )

    return result


# =====================================================================
# Phase 4-C: 후처리 검증 (8단계 물리적 검증 + SceneDecision 변환)
# =====================================================================

def validate_adjacency(scene_indices: list[int]) -> bool:
    """씬 인덱스가 빠짐없이 연속인지 검증한다."""
    if len(scene_indices) <= 1:
        return True
    sorted_indices = sorted(scene_indices)
    for i in range(1, len(sorted_indices)):
        if sorted_indices[i] != sorted_indices[i - 1] + 1:
            return False
    return True


def validate_llm_output(
    llm_output: dict,
    merge_groups: list[MergeCandidate],
    itv_candidates: list[dict],
    total_scenes: int,
    max_video_clips: int,
    oversized_indices: list[int],
) -> dict:
    """LLM 출력에 대해 8단계 물리적 검증을 수행한다.

    검증 통과한 정제된 출력을 반환한다.
    """
    group_map: dict[str, MergeCandidate] = {mg.group_id: mg for mg in merge_groups}
    itv_img_indices: set[int] = {c["index"] for c in itv_candidates}
    oversized_set: set[int] = set(oversized_indices)

    video_clips: list[dict] = llm_output.get("video_clips", [])
    static_scenes: list[dict] = llm_output.get("static_scenes", [])

    validated_clips: list[dict] = []
    used_scenes: set[int] = set()

    # ── 1. group_id 유효성 + 2. 씬 중복 검증 ──
    for clip in video_clips:
        group_id = clip.get("group_id")
        scene_index = clip.get("scene_index")  # oversized 전용

        if scene_index is not None and scene_index in oversized_set:
            # oversized 씬은 직접 인덱스로 지정
            if scene_index in used_scenes:
                logger.warning("[validate] oversized 씬 %d 중복 — 제거", scene_index)
                continue
            used_scenes.add(scene_index)
            validated_clips.append(clip)
            continue

        if group_id is None or group_id not in group_map:
            logger.warning("[validate] 검증 1: 유효하지 않은 group_id '%s' — 제거", group_id)
            continue

        mg = group_map[group_id]
        overlap = used_scenes & set(mg.scene_indices)
        if overlap:
            logger.warning(
                "[validate] 검증 2: group_id '%s' 씬 중복 %s — 제거",
                group_id, overlap,
            )
            continue

        used_scenes.update(mg.scene_indices)
        validated_clips.append(clip)

    # ── 4. 비디오 상한 검증 ──
    if len(validated_clips) > max_video_clips:
        logger.warning(
            "[validate] 검증 4: 비디오 %d개 > 상한 %d — 초과분 제거",
            len(validated_clips), max_video_clips,
        )
        validated_clips = validated_clips[:max_video_clips]
        # 재계산 used_scenes
        used_scenes = set()
        for clip in validated_clips:
            group_id = clip.get("group_id")
            scene_index = clip.get("scene_index")
            if scene_index is not None and scene_index in oversized_set:
                used_scenes.add(scene_index)
            elif group_id and group_id in group_map:
                used_scenes.update(group_map[group_id].scene_indices)

    # ── 5. ITV 이미지 매칭 검증 ──
    for clip in validated_clips:
        if clip.get("type") == "itv":
            img_id = clip.get("image_id")
            if img_id is None or img_id not in itv_img_indices:
                logger.warning(
                    "[validate] 검증 5: itv image_id %s 무효 — ttv로 전환",
                    img_id,
                )
                clip["type"] = "ttv"
                clip.pop("image_id", None)

    # ── 5.5. 댓글 포함 비디오 클립 강제 제거 ──
    comment_removed: list[dict] = []
    for clip in validated_clips:
        group_id = clip.get("group_id")
        if group_id and group_id in group_map and group_map[group_id].contains_comment:
            logger.warning(
                "[validate] 검증 5.5: 댓글 포함 비디오 클립 강제 제거: group=%s, scenes=%s",
                group_id, group_map[group_id].scene_indices,
            )
            comment_removed.append(clip)
    if comment_removed:
        validated_clips = [c for c in validated_clips if c not in comment_removed]
        # used_scenes 재계산
        used_scenes = set()
        for clip in validated_clips:
            group_id = clip.get("group_id")
            scene_index = clip.get("scene_index")
            if scene_index is not None and scene_index in oversized_set:
                used_scenes.add(scene_index)
            elif group_id and group_id in group_map:
                used_scenes.update(group_map[group_id].scene_indices)

    # ── 6. 인접성 재확인 (static_scenes) ──
    validated_static: list[dict] = []
    for entry in static_scenes:
        indices = entry.get("scene_indices", [])
        # 비디오 클립에 포함된 씬은 제거
        indices = [i for i in indices if i not in used_scenes]
        if not indices:
            continue

        if not validate_adjacency(indices):
            # 연속인 부분끼리 분리
            sorted_idx = sorted(indices)
            groups: list[list[int]] = [[sorted_idx[0]]]
            for idx in sorted_idx[1:]:
                if idx == groups[-1][-1] + 1:
                    groups[-1].append(idx)
                else:
                    groups.append([idx])
            for g in groups:
                validated_static.append({
                    "type": entry.get("type", "text_only"),
                    "scene_indices": g,
                    "reason": entry.get("reason", ""),
                })
            logger.warning(
                "[validate] 검증 6: 비연속 인덱스 %s → %d개 그룹으로 분리",
                indices, len(groups),
            )
        else:
            entry["scene_indices"] = indices
            validated_static.append(entry)

    # ── 3. 씬 누락 검증 ──
    all_assigned: set[int] = set(used_scenes)
    for entry in validated_static:
        all_assigned.update(entry.get("scene_indices", []))

    missing = set(range(total_scenes)) - all_assigned
    if missing:
        logger.warning("[validate] 검증 3: 누락 씬 %s → text_only 추가", sorted(missing))
        # 연속인 부분끼리 그룹화
        sorted_missing = sorted(missing)
        groups = [[sorted_missing[0]]]
        for idx in sorted_missing[1:]:
            if idx == groups[-1][-1] + 1:
                groups[-1].append(idx)
            else:
                groups.append([idx])
        for g in groups:
            validated_static.append({
                "type": "text_only",
                "scene_indices": g,
                "reason": "자동 추가 (누락 씬)",
            })

    return {
        "video_clips": validated_clips,
        "static_scenes": validated_static,
    }


def _convert_to_scene_decisions(
    validated_output: dict,
    body_items: list[tuple[str, str | None, str, str | None, list[str] | None]],
    merge_groups: list[MergeCandidate],
    itv_candidates: list[dict],
    body_images: list[str],
    oversized_indices: list[int],
    mood: str = "daily",
    tts_emotion: str = "",
) -> list[SceneDecision]:
    """검증된 LLM 출력을 SceneDecision 리스트로 변환한다.

    반환 순서는 씬 인덱스 순서 (오름차순).
    """
    group_map: dict[str, MergeCandidate] = {mg.group_id: mg for mg in merge_groups}
    itv_map: dict[int, dict] = {c["index"]: c for c in itv_candidates}
    oversized_set: set[int] = set(oversized_indices)

    # 각 씬 인덱스에 대한 SceneDecision 매핑 (정렬용)
    # key: 대표 인덱스 (그룹의 첫 인덱스 또는 단일 인덱스)
    decisions: dict[int, SceneDecision] = {}

    # ── 비디오 클립 변환 ──
    for clip in validated_output.get("video_clips", []):
        clip_type = clip.get("type", "ttv")
        group_id = clip.get("group_id")
        scene_index = clip.get("scene_index")

        # oversized 씬 (직접 인덱스 지정)
        if scene_index is not None and scene_index in oversized_set:
            text, voice, bt, author, psl = body_items[scene_index]
            tts_sec = estimate_tts_duration(text)
            img_url = None
            init_img = None
            v_mode = "t2v"
            v_subtype = "ttv"

            if clip_type == "itv":
                img_id = clip.get("image_id")
                if img_id is not None and img_id in itv_map:
                    img_url = itv_map[img_id]["original_url"]
                    init_img = itv_map[img_id]["local_path"]
                    v_mode = "i2v"
                    v_subtype = "itv"

            decisions[scene_index] = SceneDecision(
                type="video_text",
                text_lines=[text],
                image_url=img_url,
                mood=mood,
                tts_emotion=tts_emotion,
                voice_override=voice,
                block_type=bt,
                author=author,
                pre_split_lines=psl,
                video_mode=v_mode,
                video_init_image=init_img,
                video_subtype=v_subtype,
                estimated_tts_sec=tts_sec,
                merged_scene_indices=[scene_index],
            )
            continue

        # 일반 그룹 (merge_groups에서 가져옴)
        if group_id is None or group_id not in group_map:
            continue
        mg = group_map[group_id]

        # 병합된 씬들의 텍스트 수집
        merged_texts: list[str] = []
        merged_voices: list[str | None] = []
        has_comment = False
        for idx in mg.scene_indices:
            text, voice, bt, author, psl = body_items[idx]
            merged_texts.append(text)
            merged_voices.append(voice)
            if bt == "comment":
                has_comment = True

        img_url = None
        init_img = None
        v_mode = "t2v"
        v_subtype = "ttv"

        if clip_type == "itv":
            img_id = clip.get("image_id")
            if img_id is not None and img_id in itv_map:
                img_url = itv_map[img_id]["original_url"]
                init_img = itv_map[img_id]["local_path"]
                v_mode = "i2v"
                v_subtype = "itv"

        decisions[mg.start_index] = SceneDecision(
            type="video_text",
            text_lines=merged_texts,
            image_url=img_url,
            mood=mood,
            tts_emotion=tts_emotion,
            block_type="comment" if has_comment else "body",
            video_mode=v_mode,
            video_init_image=init_img,
            video_subtype=v_subtype,
            estimated_tts_sec=mg.total_duration_sec,
            merged_scene_indices=list(mg.scene_indices),
        )

    # ── 정적 씬 변환 ──
    for entry in validated_output.get("static_scenes", []):
        scene_type = entry.get("type", "text_only")
        indices = entry.get("scene_indices", [])
        entry_img_id = entry.get("image_id")

        for idx in indices:
            if idx in decisions:
                continue  # 이미 비디오 클립으로 할당됨
            if idx < 0 or idx >= len(body_items):
                continue

            text, voice, bt, author, psl = body_items[idx]
            img_url = None

            if scene_type == "image_text" and entry_img_id is not None:
                if 0 <= entry_img_id < len(body_images):
                    img_url = body_images[entry_img_id]
                    entry_img_id = None  # 이미지는 한 씬에만 사용

            decisions[idx] = SceneDecision(
                type=scene_type if img_url else "text_only",
                text_lines=[text],
                image_url=img_url,
                mood=mood,
                tts_emotion=tts_emotion,
                voice_override=voice,
                block_type=bt,
                author=author,
                pre_split_lines=psl,
                video_mode="static",
                estimated_tts_sec=estimate_tts_duration(text),
                merged_scene_indices=[idx],
            )

    # 인덱스 순서로 정렬하여 반환
    return [decisions[k] for k in sorted(decisions.keys())]


# =====================================================================
# Phase 4.5: video_mode 할당 (기존 — LLM Director의 안전망)
# =====================================================================

def assign_video_modes(
    scenes: list,
    image_cache_dir: Path,
    i2v_threshold: float = 0.6,
) -> list:
    """각 씬에 video_mode ("t2v" | "i2v" | "static")를 할당한다.

    distribute_images()에서 사전 할당된 본문 씬(video_mode != None)은 스킵.
    intro/outro 등 미할당 씬만 처리한다.

    할당 규칙 (미할당 씬):
    1. text_only → "t2v"
    2. image_text / image_only → image_filter 평가, score >= threshold → "i2v", 아니면 "t2v"
    3. intro / outro → "t2v"
    """
    from ai_worker.video.image_filter import evaluate_image

    for i, scene in enumerate(scenes):
        # distribute_images()에서 사전 할당된 씬 스킵
        if scene.video_mode is not None:
            continue

        if scene.type == "text_only":
            scene.video_mode = "t2v"

        elif scene.type in ("image_text", "image_only"):
            if scene.image_url:
                local_path = _download_and_cache_image(scene.image_url, image_cache_dir)
                if local_path and local_path.exists():
                    suitability = evaluate_image(local_path)
                    # extreme_aspect_ratio는 점수와 무관하게 I2V 차단
                    # (LTX-2 리사이즈 시 CUBLAS 에러 유발)
                    if "extreme_aspect_ratio" in suitability.reason:
                        scene.video_mode = "t2v"
                        logger.info(
                            "[scene] 씬 %d (%s): extreme_aspect_ratio → T2V 강제 전환 "
                            "(score=%.3f, %dx%d)",
                            i, scene.type, suitability.score,
                            suitability.width, suitability.height,
                        )
                    elif suitability.score >= i2v_threshold:
                        scene.video_mode = "i2v"
                        scene.video_init_image = str(local_path)
                        logger.info(
                            "[scene] 씬 %d (%s): I2V 선정 (score=%.3f, category=%s)",
                            i, scene.type, suitability.score, suitability.category,
                        )
                    else:
                        scene.video_mode = "t2v"
                        logger.info(
                            "[scene] 씬 %d (%s): 이미지 부적합 → T2V 전환 (score=%.3f, reason=%s)",
                            i, scene.type, suitability.score, suitability.reason,
                        )
                else:
                    scene.video_mode = "t2v"
                    logger.warning("[scene] 씬 %d: 이미지 다운로드 실패 → T2V 전환", i)
            else:
                scene.video_mode = "t2v"

        elif scene.type in ("intro", "outro"):
            scene.video_mode = "t2v"

        else:
            scene.video_mode = "t2v"

    t2v_count = sum(1 for s in scenes if s.video_mode == "t2v")
    i2v_count = sum(1 for s in scenes if s.video_mode == "i2v")
    static_count = sum(1 for s in scenes if s.video_mode == "static")
    logger.info(
        "[scene] video_mode 할당 완료: 총 %d씬 (T2V=%d, I2V=%d, 정적=%d)",
        len(scenes), t2v_count, i2v_count, static_count,
    )
    return scenes


def pick_random_file(dir_path: str, extensions: list[str]) -> Path | None:
    """지정 폴더에서 지원 확장자의 파일 하나를 랜덤 선택. 비어있거나 폴더 없으면 None."""
    folder = Path(dir_path)
    if not folder.is_dir():
        return None
    files = [f for f in folder.iterdir() if f.suffix.lower() in extensions]
    return random.choice(files) if files else None


def distribute_images(
    body_items: list[tuple[str, str | None, str, str | None, list[str] | None]],
    images: list[str],
    max_images: int,
    tts_emotion: str = "",
    mood: str = "daily",
) -> list[SceneDecision]:
    """본문 아이템에 이미지를 균등 분배 + 씬 유형 균형 배분.

    씬 유형 균형:
    - 이미지 있을 때 (1:1:1): 비디오+텍스트 : 정적텍스트 : 이미지+텍스트
    - 이미지 없을 때 (1:1): 비디오+텍스트 : 정적텍스트

    video_mode 사전 할당:
    - 비디오 씬: video_mode="t2v" (Phase 6/7에서 프롬프트+비디오 생성)
    - 정적/이미지 씬: video_mode="static" (Phase 6/7 자동 스킵)

    Args:
        body_items: (text, voice_override, block_type, author, pre_split_lines) 튜플 리스트
        images: 사용 가능한 이미지 경로 리스트
        max_images: 최대 이미지 사용 수
        tts_emotion: TTS 감정 키
        mood: 콘텐츠 mood 키
    """
    remaining_imgs = images[:max_images]

    def _make(
        type_: str, text: str, image: str | None = None,
        voice: str | None = None, block_type: str = "body", author: str | None = None,
        pre_split_lines: list[str] | None = None,
        video_mode: str | None = None,
    ) -> SceneDecision:
        sd = SceneDecision(
            type=type_,
            text_lines=[text],
            image_url=image,
            mood=mood,
            tts_emotion=tts_emotion,
            voice_override=voice,
            block_type=block_type,
            author=author,
            pre_split_lines=pre_split_lines,
        )
        sd.video_mode = video_mode
        return sd

    # Case: 텍스트 없이 이미지만
    if not body_items and remaining_imgs:
        return [_make("image_only", "", img, video_mode="static") for img in remaining_imgs]

    if not body_items:
        return []

    total = len(body_items)
    has_images = bool(remaining_imgs)

    if has_images:
        # ── 이미지 있음: 1:1:1 (비디오 : 정적텍스트 : 이미지+텍스트) ──
        n_available = len(remaining_imgs)
        base = total // 3
        remainder = total % 3
        n_video = base + (1 if remainder >= 1 else 0)
        n_image_text = min(base + (1 if remainder >= 2 else 0), n_available)
        n_static = total - n_video - n_image_text

        # image_text 위치: 균등 분배 (기존 interval 로직)
        if n_image_text > 0:
            interval = total / (n_image_text + 1)
            img_positions = sorted(
                {round(interval * (k + 1)) - 1 for k in range(n_image_text)}
            )
        else:
            img_positions = []
        img_pos_set = set(img_positions)

        # 나머지 위치에서 비디오/정적 교대 배치
        non_img_indices = [i for i in range(total) if i not in img_pos_set]
        video_positions: set[int] = set()
        place_video = True
        v_placed = 0
        s_placed = 0
        for idx in non_img_indices:
            if place_video and v_placed < n_video:
                video_positions.add(idx)
                v_placed += 1
                place_video = False
            elif s_placed < n_static:
                s_placed += 1
                place_video = True
            else:
                video_positions.add(idx)
                v_placed += 1

        # 씬 생성
        img_idx = 0
        scenes: list[SceneDecision] = []
        for line_idx, (text, voice, bt, au, psl) in enumerate(body_items):
            if line_idx in img_pos_set and img_idx < n_image_text:
                scenes.append(_make(
                    "image_text", text, remaining_imgs[img_idx],
                    voice, bt, au, psl, video_mode="static",
                ))
                img_idx += 1
            elif line_idx in video_positions:
                scenes.append(_make(
                    "text_only", text, voice=voice, block_type=bt,
                    author=au, pre_split_lines=psl, video_mode="t2v",
                ))
            else:
                scenes.append(_make(
                    "text_only", text, voice=voice, block_type=bt,
                    author=au, pre_split_lines=psl, video_mode="static",
                ))
    else:
        # ── 이미지 없음: 1:1 (비디오 : 정적텍스트) ──
        n_video = (total + 1) // 2
        n_static = total - n_video

        scenes = []
        place_video = True
        v_placed = 0
        s_placed = 0
        for text, voice, bt, au, psl in body_items:
            if place_video and v_placed < n_video:
                scenes.append(_make(
                    "text_only", text, voice=voice, block_type=bt,
                    author=au, pre_split_lines=psl, video_mode="t2v",
                ))
                v_placed += 1
                place_video = False
            elif s_placed < n_static:
                scenes.append(_make(
                    "text_only", text, voice=voice, block_type=bt,
                    author=au, pre_split_lines=psl, video_mode="static",
                ))
                s_placed += 1
                place_video = True
            else:
                scenes.append(_make(
                    "text_only", text, voice=voice, block_type=bt,
                    author=au, pre_split_lines=psl, video_mode="t2v",
                ))
                v_placed += 1

    n_v = sum(1 for s in scenes if s.video_mode == "t2v")
    n_s = sum(1 for s in scenes if s.video_mode == "static" and s.type == "text_only")
    n_i = sum(1 for s in scenes if s.type == "image_text")
    logger.info(
        "[scene] 본문 씬 배분: 총 %d씬 (비디오=%d, 정적=%d, 이미지=%d)",
        len(scenes), n_v, n_s, n_i,
    )
    return scenes


class SceneDirector:
    """scene_policy.json 정책과 mood를 기반으로 씬 목록을 결정한다.

    LLM 모드(mode="llm"): Phase 4-A/B/C 파이프라인으로 비디오/정적 씬 배분.
    Rule-based 모드(mode="rule_based"): 기존 distribute_images() 로직 사용.
    """

    def __init__(
        self,
        profile: ResourceProfile,
        images: list[str],
        script: dict,
        comment_voices: list[str] | None = None,
        mood: str = "daily",
        post_id: int | None = None,
        image_cache_dir: Path | None = None,
    ) -> None:
        self.profile = profile
        self._images: list[str] = list(images)   # 소모 추적용 복사본
        self.script = script
        self.mood = mood
        self.post_id = post_id
        self.image_cache_dir = image_cache_dir

        if comment_voices is None:
            # pipeline.json에서 자동 로드 (processor.py 레거시 경로용)
            try:
                from config.settings import load_pipeline_config
                _cfg = load_pipeline_config()
                self.comment_voices = _json.loads(_cfg.get("comment_voices", "[]"))
            except Exception:
                self.comment_voices = []
        else:
            self.comment_voices = comment_voices

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def direct(self) -> list[SceneDecision]:
        """씬 배분 목록을 생성해 반환한다 (scene_policy.json 기반)."""
        import json as _json
        from pathlib import Path as _Path

        # scene_policy.json 로드
        policy_path = _Path("config/scene_policy.json")
        try:
            policy = _json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("scene_policy.json 로드 실패, fallback 모드: %s", e)
            policy = None

        mood = self.mood
        fallback_mood = "daily"

        if policy:
            moods_dict = policy.get("moods", {})
            supported_image_ext = policy.get("defaults", {}).get("supported_image_ext", [".png", ".jpg", ".jpeg", ".webp"])
            supported_bgm_ext = policy.get("defaults", {}).get("supported_bgm_ext", [".mp3", ".wav", ".ogg"])
            max_body_images = policy.get("defaults", {}).get("max_body_images", 8)
            fallback_mood = policy.get("defaults", {}).get("fallback_mood", "daily")
            fixed_texts = policy.get("scene_rules", {}).get("outro", {}).get("fixed_texts", ["여러분들의 생각은 어떤가요?"])

            # mood 프리셋 조회 (없으면 fallback)
            if mood not in moods_dict:
                logger.warning("mood '%s' 미인식, fallback '%s' 사용", mood, fallback_mood)
                mood = fallback_mood
            preset = moods_dict.get(mood, moods_dict.get(fallback_mood, {}))
            tts_emotion = preset.get("tts_emotion", "")

            def _pick_asset(dir_key: str) -> _Path | None:
                dir_path = preset.get(dir_key, "")
                result = pick_random_file(dir_path, supported_image_ext)
                if result is None and mood != fallback_mood:
                    # fallback mood 폴더 시도
                    fb_preset = moods_dict.get(fallback_mood, {})
                    result = pick_random_file(fb_preset.get(dir_key, ""), supported_image_ext)
                return result

            def _pick_bgm() -> _Path | None:
                # BGM 폴더가 비어있으면 fallback 없이 None 반환 → BGM 미사용
                return pick_random_file(preset.get("bgm_dir", ""), supported_bgm_ext)
        else:
            # policy 없을 때 fallback (기존 동작 유지)
            tts_emotion = ""
            max_body_images = 20
            fixed_texts = ["여러분들의 생각은 어떤가요?"]

            def _pick_asset(_: str) -> None:
                return None

            def _pick_bgm() -> None:
                return None

        scenes: list[SceneDecision] = []

        # BGM 선택 (intro 씬에만 설정)
        bgm_file = _pick_bgm()
        bgm_path = str(bgm_file) if bgm_file else None

        # ── Intro ──────────────────────────────────────────────────────
        hook = self.script.get("hook", "")
        if self._images:
            # 이미지 있으면 첫 이미지 사용
            intro_img = self._images.pop(0)
            intro_type = "image_text"
        else:
            # 이미지 없으면 mood 폴더에서 랜덤 (로컬 에셋이므로 타입은 항상 intro)
            intro_asset = _pick_asset("intro_image_dir")
            intro_img = str(intro_asset) if intro_asset else None
            intro_type = "intro"

        scenes.append(SceneDecision(
            type=intro_type,
            text_lines=[hook],
            image_url=intro_img,
            mood=mood,
            tts_emotion=tts_emotion,
            bgm_path=bgm_path,
        ))

        # ── Body ───────────────────────────────────────────────────────
        body_raw = list(self.script.get("body", []))
        body_items: list[tuple[str, str | None, str, str | None, list[str] | None]] = []
        for item in body_raw:
            if isinstance(item, dict):
                lines_raw = item.get("lines", [])
                text = " ".join(lines_raw)
                block_type = item.get("type", "body")
                author = item.get("author")
                is_comment = block_type == "comment"
                voice = random.choice(self.comment_voices) if is_comment and self.comment_voices else None
                body_items.append((text, voice, block_type, author, lines_raw if len(lines_raw) > 1 else None))
            else:
                body_items.append((str(item), None, "body", None, None))

        # 모드 결정: LLM vs rule_based
        director_mode = get_domain_setting(
            "scene", "scene_director", "mode", default="rule_based",
        )
        from config.settings import VIDEO_GEN_ENABLED

        if director_mode == "llm" and VIDEO_GEN_ENABLED:
            body_scenes = self._llm_direct_body(
                body_items=body_items,
                body_images=list(self._images),
                tts_emotion=tts_emotion,
                mood=mood,
                max_body_images=max_body_images,
            )
            if body_scenes is None:
                # LLM 실패 시 rule_based 폴백
                logger.warning("[scene_director] LLM 실패 — rule_based 폴백")
                body_scenes = distribute_images(
                    body_items, list(self._images), max_body_images,
                    tts_emotion=tts_emotion, mood=mood,
                )
        else:
            body_scenes = distribute_images(
                body_items, list(self._images), max_body_images,
                tts_emotion=tts_emotion, mood=mood,
            )

        # _images에서 사용된 이미지 소모 추적
        used_img_count = sum(1 for s in body_scenes if s.image_url is not None)
        self._images = self._images[used_img_count:]
        scenes.extend(body_scenes)

        # ── Outro ──────────────────────────────────────────────────────
        outro_asset = _pick_asset("outro_image_dir")
        outro_img = str(outro_asset) if outro_asset else None
        outro_text = random.choice(fixed_texts)
        scenes.append(SceneDecision(
            type="outro",
            text_lines=[outro_text],
            image_url=outro_img,
            mood=mood,
            tts_emotion=tts_emotion,
        ))

        logger.debug(
            "씬 배분: 총 %d개 (%s) [mood=%s, tts_emotion=%s, bgm=%s]",
            len(scenes),
            ", ".join(s.type for s in scenes),
            mood,
            tts_emotion,
            bgm_path,
        )
        return scenes

    # ------------------------------------------------------------------
    # LLM Scene Director (Phase 4-A → 4-B → 4-C)
    # ------------------------------------------------------------------

    def _llm_direct_body(
        self,
        body_items: list[tuple[str, str | None, str, str | None, list[str] | None]],
        body_images: list[str],
        tts_emotion: str = "",
        mood: str = "daily",
        max_body_images: int = 8,
    ) -> list[SceneDecision] | None:
        """LLM 기반 Phase 4 파이프라인으로 본문 씬을 배분한다.

        Returns:
            SceneDecision 리스트. LLM 실패 시 None (폴백 필요).
        """
        if not body_items:
            return []

        # ── 설정 로드 ──
        sd_cfg_base = "scene_director"
        max_video_clips = get_domain_setting(
            "scene", sd_cfg_base, "max_video_clips", default=5,
        )
        min_clip_dur = get_domain_setting(
            "scene", sd_cfg_base, "target_clip_duration", "min", default=3.5,
        )
        max_clip_dur = get_domain_setting(
            "scene", sd_cfg_base, "target_clip_duration", "max", default=5.0,
        )
        max_group_size = get_domain_setting(
            "scene", sd_cfg_base, "max_group_size", default=5,
        )
        itv_threshold = get_domain_setting(
            "scene", sd_cfg_base, "itv_score_threshold", default=0.6,
        )
        llm_timeout = get_domain_setting(
            "scene", sd_cfg_base, "llm_timeout_sec", default=60,
        )
        fallback_on_fail = get_domain_setting(
            "scene", sd_cfg_base, "fallback_on_llm_fail", default=True,
        )

        # ── Phase 4-A: 사전 계산 ──
        logger.info("[scene_director] Phase 4-A: 사전 계산 시작 (body=%d)", len(body_items))

        # 씬 데이터 구성 (TTS 예상 시간 포함)
        scenes_data: list[dict] = []
        for i, (text, _voice, bt, _author, _psl) in enumerate(body_items):
            scenes_data.append({
                "index": i,
                "text": text,
                "estimated_tts_sec": estimate_tts_duration(text),
                "block_type": bt,
            })

        # 병합 가능 그룹 후보 생성
        merge_groups, oversized_indices = generate_merge_candidates_with_oversized(
            scenes_data,
            min_duration=min_clip_dur,
            max_duration=max_clip_dur,
            max_group_size=max_group_size,
        )
        logger.info(
            "[scene_director] Phase 4-A: 병합 그룹 %d개, 5초초과 씬 %d개",
            len(merge_groups), len(oversized_indices),
        )

        # merge_groups가 비어있고 oversized도 없으면 → LLM 스킵, 전체 text_only
        if not merge_groups and not oversized_indices:
            logger.info("[scene_director] 병합 후보 없음 — 전체 text_only 배분")
            return self._all_text_only(body_items, body_images, tts_emotion, mood, max_body_images)

        # ITV 이미지 사전 필터링
        itv_candidates: list[dict] = []
        if body_images and self.image_cache_dir:
            self.image_cache_dir.mkdir(parents=True, exist_ok=True)
            itv_candidates = filter_itv_candidates(
                body_images, self.image_cache_dir, threshold=itv_threshold,
            )
            logger.info(
                "[scene_director] Phase 4-A: ITV 후보 %d개 / 전체 이미지 %d개",
                len(itv_candidates), len(body_images),
            )

        # LLM 입력 데이터 구성
        llm_input = _build_llm_input(
            body_items, merge_groups, oversized_indices,
            itv_candidates, max_video_clips,
        )

        # ── Phase 4-B: LLM 디렉팅 ──
        logger.info("[scene_director] Phase 4-B: LLM 호출 시작")
        llm_output = _call_scene_director_llm(
            llm_input, timeout=llm_timeout, post_id=self.post_id,
        )

        if llm_output is None:
            if fallback_on_fail:
                return None  # 폴백 트리거
            logger.warning("[scene_director] LLM 실패, 폴백 비활성 — 전체 text_only")
            return self._all_text_only(body_items, body_images, tts_emotion, mood, max_body_images)

        # ── Phase 4-C: 후처리 검증 ──
        logger.info("[scene_director] Phase 4-C: 후처리 검증 시작")
        validated = validate_llm_output(
            llm_output, merge_groups, itv_candidates,
            total_scenes=len(body_items),
            max_video_clips=max_video_clips,
            oversized_indices=oversized_indices,
        )

        # SceneDecision 변환
        body_scenes = _convert_to_scene_decisions(
            validated, body_items, merge_groups, itv_candidates,
            body_images, oversized_indices,
            mood=mood, tts_emotion=tts_emotion,
        )

        # 결과 로깅
        n_video = sum(1 for s in body_scenes if s.type == "video_text")
        n_static = sum(1 for s in body_scenes if s.type in ("text_only", "image_text"))
        n_itv = sum(1 for s in body_scenes if s.video_subtype == "itv")
        n_ttv = sum(1 for s in body_scenes if s.video_subtype == "ttv")
        logger.info(
            "[scene_director] Phase 4 완료: %d씬 (비디오=%d [ITV=%d, TTV=%d], 정적=%d)",
            len(body_scenes), n_video, n_itv, n_ttv, n_static,
        )
        return body_scenes

    def _all_text_only(
        self,
        body_items: list[tuple[str, str | None, str, str | None, list[str] | None]],
        body_images: list[str],
        tts_emotion: str,
        mood: str,
        max_body_images: int,
    ) -> list[SceneDecision]:
        """모든 본문 씬을 text_only/image_text로 배분한다 (비디오 없음)."""
        scenes: list[SceneDecision] = []
        img_idx = 0
        for text, voice, bt, author, psl in body_items:
            if img_idx < len(body_images) and img_idx < max_body_images:
                scenes.append(SceneDecision(
                    type="image_text",
                    text_lines=[text],
                    image_url=body_images[img_idx],
                    mood=mood,
                    tts_emotion=tts_emotion,
                    voice_override=voice,
                    block_type=bt,
                    author=author,
                    pre_split_lines=psl,
                    video_mode="static",
                    estimated_tts_sec=estimate_tts_duration(text),
                ))
                img_idx += 1
            else:
                scenes.append(SceneDecision(
                    type="text_only",
                    text_lines=[text],
                    image_url=None,
                    mood=mood,
                    tts_emotion=tts_emotion,
                    voice_override=voice,
                    block_type=bt,
                    author=author,
                    pre_split_lines=psl,
                    video_mode="static",
                    estimated_tts_sec=estimate_tts_duration(text),
                ))
        return scenes

    # ------------------------------------------------------------------
    # Private helpers (레거시 — distribute_images()로 대체됨, 하위 호환 유지)
    # ------------------------------------------------------------------

    def _make_scene(
        self,
        type_: str,
        lines: list[str],
        image: str | None = None,
        stack: int = 1,
        voice_override: str | None = None,
    ) -> SceneDecision:
        """SceneDecision을 생성하며 emotion_tag를 자동 할당한다."""
        return SceneDecision(
            type=type_,
            text_lines=lines,
            image_url=image,
            text_only_stack=stack,
            emotion_tag=EMOTION_TAGS.get(type_, ""),
            voice_override=voice_override,
            mood=self.mood,
        )
