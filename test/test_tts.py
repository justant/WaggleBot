"""Fish Speech TTS 단독 테스트.

실행: python test/test_tts.py

사전 조건:
  - docker compose up fish-speech 으로 서버 기동
  - assets/voices/korean_man_default.wav 파일 준비
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_worker.tts_worker import synthesize, wait_for_fish_speech


async def main() -> None:
    print("Fish Speech 서버 확인 중...")
    ready = await wait_for_fish_speech(retries=3, delay=2.0)
    if not ready:
        print("Fish Speech 서버에 연결할 수 없습니다.")
        print("docker compose up fish-speech 을 먼저 실행하세요.")
        return

    test_cases = [
        ("intro",     "이거 진짜 충격적인 사건인데, 들어봐."),
        ("img_text",  "그 날 오후 세 시, 편의점 앞에서 벌어진 일이야."),
        ("text_only", "근데 알고보니 그 사람이 바로 옆집 아저씨였던 거지."),
        ("outro",     "어때, 신기하지? 좋아요 눌러주면 더 가져올게."),
    ]

    for scene_type, text in test_cases:
        print(f"\n[{scene_type}] {text[:30]}...")
        try:
            path = await synthesize(text=text, scene_type=scene_type)
            size_kb = path.stat().st_size // 1024
            print(f"  → 생성 완료: {path} ({size_kb}KB)")
        except Exception as e:
            print(f"  → 실패: {e}")


if __name__ == "__main__":
    asyncio.run(main())
