"""Local JARVIS speech output using ElevenLabs with a Windows TTS fallback."""
from __future__ import annotations

import json
import os
import threading

import pyttsx3
import sounddevice as sd
from dotenv import load_dotenv
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVENLABS_LANGUAGE = os.getenv("ELEVENLABS_LANGUAGE", "id")

_interrupt_event = threading.Event()
_elevenlabs_quota_exhausted = False


def _clear_console_keys() -> None:
    if os.name != "nt":
        return
    import msvcrt
    while msvcrt.kbhit():
        msvcrt.getwch()


def _speech_interrupted() -> bool:
    if os.name != "nt":
        return _interrupt_event.is_set()
    import msvcrt
    if msvcrt.kbhit():
        key = msvcrt.getwch()
        if key == "\x1b":
            _interrupt_event.set()
            return True
    return _interrupt_event.is_set()


def _voice_metadata(voice) -> str:
    parts = [
        getattr(voice, "name", ""),
        getattr(voice, "id", ""),
    ]
    for language in getattr(voice, "languages", []) or []:
        if isinstance(language, bytes):
            try:
                language = language.decode("utf-8", errors="ignore")
            except Exception:
                language = str(language)
        parts.append(str(language))
    return " ".join(parts).lower()


def _is_indonesian_voice(voice) -> bool:
    meta = _voice_metadata(voice)
    return any(token in meta for token in (
        "indonesia",
        "indonesian",
        "id-id",
        "id_id",
        "id-id",
        "ind",
    ))


def _fallback_speak(text: str) -> None:
    # Buat engine baru setiap utterance agar event loop pyttsx3 tidak bentrok.
    engine = pyttsx3.init()
    engine.setProperty("rate", 172)
    engine.setProperty("volume", 1.0)

    voices = engine.getProperty("voices") or []
    chosen_voice = next((voice for voice in voices if _is_indonesian_voice(voice)), None)

    if chosen_voice:
        engine.setProperty("voice", chosen_voice.id)
        print(f"[TTS] Windows voice: {getattr(chosen_voice, 'name', chosen_voice.id)}")
    else:
        print("[TTS] Voice Bahasa Indonesia tidak ditemukan. Menggunakan voice Windows default.")
        print("[TTS] Voice tersedia:")
        for voice in voices:
            print(f"       - {getattr(voice, 'name', voice.id)} | {getattr(voice, 'id', '')}")

    try:
        engine.say(text)
        engine.runAndWait()
    except RuntimeError as exc:
        print(f"[TTS] Windows TTS error: {exc}")
    finally:
        try:
            engine.stop()
        except Exception:
            pass


def _elevenlabs_speak(text: str) -> bool:
    global _elevenlabs_quota_exhausted
    if _elevenlabs_quota_exhausted or not ELEVENLABS_API_KEY:
        return False
    if not ELEVENLABS_API_KEY.startswith("sk_"):
        print("[TTS] ELEVENLABS_API_KEY bukan secret key.")
        return False

    payload = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "language_code": ELEVENLABS_LANGUAGE,
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.78,
            "style": 0.10,
            "use_speaker_boost": True,
        },
    }).encode("utf-8")

    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}?output_format=pcm_24000",
        data=payload,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            pcm = response.read()

        with sd.RawOutputStream(samplerate=24000, channels=1, dtype="int16") as stream:
            chunk_size = 24000 * 2 // 4
            for start in range(0, len(pcm), chunk_size):
                if _speech_interrupted():
                    stream.stop()
                    print("[TTS] Speech interrupted by ESC.")
                    return True
                stream.write(pcm[start:start + chunk_size])
        return True

    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        print(f"[TTS] ElevenLabs gagal ({exc.code}): {detail[:500]}")
        if "quota_exceeded" in detail.lower() or "exceeds your quota" in detail.lower():
            _elevenlabs_quota_exhausted = True
            print("[TTS] Kuota ElevenLabs habis. Beralih ke Windows TTS.")
        return False

    except (URLError, OSError, sd.PortAudioError) as exc:
        print(f"[TTS] ElevenLabs gagal, fallback ke suara lokal: {exc}")
        return False


def speak(text: str) -> None:
    text = str(text).strip()
    if not text:
        return

    _interrupt_event.clear()
    _clear_console_keys()
    print(f"JARVIS: {text}")

    if not _elevenlabs_speak(text):
        _fallback_speak(text)
