from pathlib import Path

from ai_worker.tts.base import BaseTTS
from ai_worker.tts.edge_tts import EdgeTTS
from ai_worker.tts.kokoro import KokoroTTS
from ai_worker.tts.gptsovits import GptSoVITS


class FishSpeechTTS(BaseTTS):
    """Fish Speech 1.5 zero-shot 클로닝 TTS — BaseTTS 어댑터.

    tts_worker.synthesize()에 위임하여 편집실 TTS 미리듣기 등
    get_tts_engine() 경로에서도 Fish Speech를 사용할 수 있도록 한다.
    """

    async def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        from ai_worker.tts.fish_client import synthesize as _synthesize
        return await _synthesize(text=text, voice_key=voice_id, output_path=output_path)


TTS_ENGINES: dict[str, type[BaseTTS]] = {
    "fish-speech": FishSpeechTTS,
    "edge-tts": EdgeTTS,
    "kokoro": KokoroTTS,
    "gpt-sovits": GptSoVITS,
}


def get_tts_engine(name: str) -> BaseTTS:
    cls = TTS_ENGINES.get(name)
    if cls is None:
        raise ValueError(f"Unknown TTS engine: {name!r}  (available: {list(TTS_ENGINES)})")
    return cls()
