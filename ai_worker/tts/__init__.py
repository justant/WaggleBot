from ai_worker.tts.base import BaseTTS
from ai_worker.tts.edge_tts import EdgeTTS
from ai_worker.tts.kokoro import KokoroTTS
from ai_worker.tts.gptsovits import GptSoVITS

TTS_ENGINES: dict[str, type[BaseTTS]] = {
    "edge-tts": EdgeTTS,
    "kokoro": KokoroTTS,
    "gpt-sovits": GptSoVITS,
}


def get_tts_engine(name: str) -> BaseTTS:
    cls = TTS_ENGINES.get(name)
    if cls is None:
        raise ValueError(f"Unknown TTS engine: {name!r}  (available: {list(TTS_ENGINES)})")
    return cls()
