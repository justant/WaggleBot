from pathlib import Path

from ai_worker.tts.base import BaseTTS
from ai_worker.tts.edge_tts import EdgeTTS


class FishSpeechTTS(BaseTTS):
    """Fish Speech 1.5 zero-shot 클로닝 TTS — BaseTTS 어댑터."""

    async def synthesize(self, text: str, voice_id: str, output_path: Path) -> Path:
        from ai_worker.tts.fish_client import synthesize as _synthesize
        return await _synthesize(text=text, voice_key=voice_id, output_path=output_path)


TTS_ENGINES: dict[str, type[BaseTTS]] = {
    "fish-speech": FishSpeechTTS,
    "edge-tts": EdgeTTS,
}


def get_tts_engine(name: str) -> BaseTTS:
    cls = TTS_ENGINES.get(name)
    if cls is None:
        raise ValueError(f"Unknown TTS engine: {name!r}  (available: {list(TTS_ENGINES)})")
    return cls()
