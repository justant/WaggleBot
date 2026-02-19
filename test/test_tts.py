"""Fish Speech TTS 단독 테스트.

실행: python test/test_tts.py [voice_key ...]

  python test/test_tts.py              # 등록된 모든 음성 테스트
  python test/test_tts.py anna yohan   # 특정 음성만 테스트
  python test/test_tts.py default      # 기본 음성만 테스트

사전 조건:
  - docker compose up fish-speech 으로 서버 기동
  - assets/voices/ 내 참조 오디오 파일 준비
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import VOICE_PRESETS, VOICE_REFERENCE_TEXTS
from ai_worker.tts.fish_client import synthesize, wait_for_fish_speech

# 모든 음성에 공통으로 합성할 테스트 문장 (4가지 scene_type 각 1개)
_TEST_CASES: list[tuple[str, str]] = [
    ("intro",     "여돌 논란은 남돌에겐 개꿀이네?"),
    ("text_only1",  "여돌 사건이 터졌으면 여론이 빠르게 돌아"),
    ("text_only2",  "남돌은 사실확인 전에도 칭찬 물결"),
    ("text_only3", "아이린처럼 여돌은 악플로 베스트 댓글 도배"),
    ("text_only4", "베댓 'ㅇㅇ': 남돌 진짜 살기 편하다 ㅋㅋ"),
    ("outro",     "여러분들의 생각은 어떤가요?"),
]


async def test_voice(voice_key: str) -> None:
    """단일 voice_key에 대해 4개 씬 타입 합성 후 결과를 출력한다."""
    ref_file = VOICE_PRESETS.get(voice_key, "")
    ref_text_preview = VOICE_REFERENCE_TEXTS.get(voice_key, "")[:30]
    print(f"\n{'='*60}")
    print(f"[{voice_key}]  파일: {ref_file}")
    print(f"           참조텍스트: {ref_text_preview}...")
    print(f"{'='*60}")

    for scene_type, text in _TEST_CASES:
        print(f"\n  [{scene_type}] {text}")
        try:
            out = Path(f"/tmp/{voice_key}_{scene_type}.wav")
            path = await synthesize(text=text, scene_type=scene_type, voice_key=voice_key, output_path=out)
            size_kb = path.stat().st_size // 1024
            print(f"    → {path}  ({size_kb}KB)")
        except Exception as e:
            print(f"    → 실패: {e}")


async def main() -> None:
    # 테스트 대상 voice_key 결정
    requested = sys.argv[1:]
    if requested:
        unknown = [k for k in requested if k not in VOICE_PRESETS]
        if unknown:
            print(f"알 수 없는 voice_key: {unknown}")
            print(f"사용 가능: {list(VOICE_PRESETS.keys())}")
            return
        targets = requested
    else:
        targets = list(VOICE_PRESETS.keys())

    print("Fish Speech 서버 확인 중...")
    ready = await wait_for_fish_speech(retries=3, delay=2.0)
    if not ready:
        print("Fish Speech 서버에 연결할 수 없습니다.")
        print("docker compose up fish-speech 을 먼저 실행하세요.")
        return

    print(f"\n테스트 대상 음성: {targets}")
    for voice_key in targets:
        await test_voice(voice_key)

    print(f"\n\n{'='*60}")
    print("테스트 완료. /tmp/tts_*.wav 파일을 재생해서 품질을 확인하세요.")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
