"""Edge-TTS 단독 테스트.

실행: python test/test_tts.py

사전 조건:
  - 인터넷 연결 (Edge-TTS는 Microsoft 클라우드 사용)
  - edge-tts 패키지 설치: pip install edge-tts
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_worker.tts import get_tts_engine


async def main() -> None:
    engine = get_tts_engine("edge-tts")
    voice_id = "ko-KR-SunHiNeural"

    test_cases = [
        ("intro",     "이거 진짜 충격적인 사건인데, 들어봐."),
        ("img_text",  "그 날 오후 세 시, 편의점 앞에서 벌어진 일이야."),
        ("text_only", "근데 알고보니 그 사람이 바로 옆집 아저씨였던 거지."),
        ("outro",     "어때, 신기하지? 좋아요 눌러주면 더 가져올게."),
    ]

    for scene_type, text in test_cases:
        print(f"\n[{scene_type}] {text[:30]}...")
        output_path = Path(f"/tmp/test_tts_{scene_type}.wav")
        try:
            path = await engine.synthesize(text=text, voice_id=voice_id, output_path=output_path)
            size_kb = path.stat().st_size // 1024
            print(f"  → 생성 완료: {path} ({size_kb}KB)")
        except Exception as e:
            print(f"  → 실패: {e}")


if __name__ == "__main__":
    asyncio.run(main())
