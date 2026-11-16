"""Package vocal Jarvis : reconnaissance locale (STT), synthèse (TTS) et pont web."""

from backend.voice import pont_web, stt, tts

__all__ = ["pont_web", "stt", "tts"]
