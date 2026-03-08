"""layout.json 글자수 제한 시각 테스트 — body(20자×2줄) / comment(20자×3줄).

글자수를 꽉 채우거나 넘기는 경계 케이스를 text_only 프레임으로 렌더링하여
_result/test_layout_chars/ 에 PNG 스크린샷을 저장한다.

사용법:
    python test/test_layout_chars.py
"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ai_worker.renderer.layout import (
    _create_base_frame,
    _render_text_only_frame,
    _load_font,
    _wrap_korean,
)

LAYOUT_PATH = _PROJECT_ROOT / "config" / "layout.json"
FONT_DIR = _PROJECT_ROOT / "assets" / "fonts"
OUT_DIR = _PROJECT_ROOT / "_result" / "test_layout_chars"
OUT_DIR.mkdir(parents=True, exist_ok=True)

layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))

TITLE = "글자수 제한 레이아웃 테스트"

# ── 테스트 케이스 ──
# body: 20자×2줄 / comment: 20자×3줄
CASES = [
    # ================================================================
    # BODY 케이스
    # ================================================================
    {
        "name": "body_1line_exact20",
        "desc": "본문 1줄 정확히 20자",
        "history": [
            {
                "lines": ["가나다라마바사아자차카타파하가나다라마바"],  # 20자
                "is_new": True,
                "block_type": "body",
                "author": None,
            },
        ],
    },
    {
        "name": "body_2line_exact20",
        "desc": "본문 2줄 각 20자 (꽉 참 — 40자)",
        "history": [
            {
                "lines": [
                    "오늘회사에서정말어이없는일이있었는데요",  # 19자
                    "보면깜짝놀랄거예요저도아직믿기지않거든",  # 19자
                ],
                "is_new": True,
                "block_type": "body",
                "author": None,
            },
        ],
    },
    {
        "name": "body_2line_20each",
        "desc": "본문 2줄 각 정확히 20자 (최대 길이)",
        "history": [
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바",  # 20자
                    "아버지가방에들어가셨다어머니께서부엌에서",  # 20자
                ],
                "is_new": True,
                "block_type": "body",
                "author": None,
            },
        ],
    },
    {
        "name": "body_overflow_25",
        "desc": "본문 1줄 25자 (20자 초과 — 넘침 확인)",
        "history": [
            {
                "lines": ["가나다라마바사아자차카타파하가나다라마바사아자차카"],  # 25자
                "is_new": True,
                "block_type": "body",
                "author": None,
            },
        ],
    },
    {
        "name": "body_stack3_mixed",
        "desc": "본문 3슬롯 스택 (이전2+현재1), 각 줄 다양한 길이",
        "history": [
            {
                "lines": ["첫번째 씬 텍스트입니다"],  # ~11자
                "is_new": False,
                "block_type": "body",
                "author": None,
            },
            {
                "lines": ["두번째씬은조금더길게작성", "이어지는내용작성합니다"],
                "is_new": False,
                "block_type": "body",
                "author": None,
            },
            {
                "lines": ["가나다라마바사아자차카타파하가나다라마바"],  # 20자
                "is_new": True,
                "block_type": "body",
                "author": None,
            },
        ],
    },
    # ================================================================
    # COMMENT 케이스
    # ================================================================
    {
        "name": "comment_1line_exact20",
        "desc": "댓글 1줄 정확히 20자",
        "history": [
            {
                "lines": ["이건진짜공감합니다저도같은경험했어요ㅋ"],  # 19자
                "is_new": True,
                "block_type": "comment",
                "author": "베스트유저",
            },
        ],
    },
    {
        "name": "comment_2line_20each",
        "desc": "댓글 2줄 각 20자 (40자)",
        "history": [
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바",  # 20자
                    "아버지가방에들어가셨다어머니께서부엌에서",  # 20자
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "닉네임테스트",
            },
        ],
    },
    {
        "name": "comment_3line_20each",
        "desc": "댓글 3줄 각 20자 (60자 최대 — 꽉 참)",
        "history": [
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바",  # 20자
                    "아버지가방에들어가셨다어머니께서부엌에서",  # 20자
                    "하늘땅바다산강호수계곡폭포나무꽃잔디바위",  # 20자
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "긴닉네임사용자",
            },
        ],
    },
    {
        "name": "comment_3line_overflow",
        "desc": "댓글 3줄 + 25자 (초과 — 넘침 확인)",
        "history": [
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바사아자차카",  # 25자
                    "아버지가방에들어가셨다어머니께서는부엌에서요리를하",  # 25자
                    "하늘땅바다산강호수계곡폭포나무꽃잔디바위돌모래흙물",  # 25자
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "테스트유저",
            },
        ],
    },
    {
        "name": "comment_realistic_short",
        "desc": "댓글 실제 짧은 예시 (1줄, 11자)",
        "history": [
            {
                "lines": ["ㅋㅋㅋㅋ 진짜 웃기네요"],
                "is_new": True,
                "block_type": "comment",
                "author": "웃음참기",
            },
        ],
    },
    {
        "name": "comment_realistic_60chars",
        "desc": "댓글 실제 60자 (20×3줄 꽉 참 — 자연어)",
        "history": [
            {
                "lines": [
                    "진짜 이거 보면서 소름 돋았음",  # 15자
                    "실제로 일어날 수 있다니 무섭",  # 15자
                    "나였으면 바로 도망갔을 듯 ㄷㄷ",  # 16자
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "공감러",
            },
        ],
    },
    # ================================================================
    # 혼합 케이스 (body + comment 스택)
    # ================================================================
    {
        "name": "mixed_body_comment_stack",
        "desc": "본문(2줄) + 댓글(3줄) 스택 — 공간 점유 확인",
        "history": [
            {
                "lines": [
                    "이야기의 마무리 부분입니다",
                    "모두 해결 해피엔딩이었대요",
                ],
                "is_new": False,
                "block_type": "body",
                "author": None,
            },
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바",  # 20자
                    "아버지가방에들어가셨다어머니께서부엌에서",  # 20자
                    "하늘땅바다산강호수계곡폭포나무꽃잔디바위",  # 20자
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "베스트댓글",
            },
        ],
    },
    {
        "name": "mixed_3comments",
        "desc": "댓글 3개 연속 스택 (각 1~3줄)",
        "history": [
            {
                "lines": ["짧은 댓글입니다"],
                "is_new": False,
                "block_type": "comment",
                "author": "유저1",
            },
            {
                "lines": [
                    "중간 길이 댓글 이 정도면",
                    "적당하게 두 줄 나뉘겠죠?",
                ],
                "is_new": False,
                "block_type": "comment",
                "author": "유저2",
            },
            {
                "lines": [
                    "가나다라마바사아자차카타파하가나다라마바",
                    "아버지가방에들어가셨다어머니께서부엌에서",
                    "하늘땅바다산강호수계곡폭포나무꽃잔디바위",
                ],
                "is_new": True,
                "block_type": "comment",
                "author": "유저3",
            },
        ],
    },
]


def main() -> None:
    print(f"출력 디렉토리: {OUT_DIR}")
    print(f"layout.json constraints: {json.dumps(layout.get('constraints', {}), ensure_ascii=False, indent=2)}\n")

    base = _create_base_frame(layout, TITLE, FONT_DIR, FONT_DIR.parent)

    total = 0
    for case in CASES:
        name = case["name"]
        desc = case["desc"]
        history = case["history"]

        # 각 줄의 글자수 표시
        char_info = []
        for entry in history:
            bt = entry["block_type"]
            for line in entry["lines"]:
                char_info.append(f"{bt}:{len(line)}자")

        out_path = OUT_DIR / f"{name}.png"
        _render_text_only_frame(base, history, layout, FONT_DIR, out_path)
        total += 1
        print(f"  [OK] {name}.png — {desc} ({', '.join(char_info)})")

    print(f"\n완료: {total}개 PNG 생성 → {OUT_DIR}")


if __name__ == "__main__":
    main()
