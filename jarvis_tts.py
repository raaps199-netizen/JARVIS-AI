"""Local JARVIS speech output using ElevenLabs with a Windows TTS fallback."""
from __future__ import annotations

import json
import os

import pyttsx3
import sounddevice as sd
from dotenv import load_dotenv
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

_fallback = pyttsx3.init()
_fallback.setProperty("rate", 172)
_fallback.setProperty("volume", 1.0)


def _fallback_speak(text: str) -> None:
    _fallback.say(text)
    _fallback.runAndWait()


def _elevenlabs_speak(text: str) -> bool:
    if not ELEVENLABS_API_KEY:
        return False

    payload = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.78,
            "style": 0.20,
            "use_speaker_boost": True,
        },
    }).encode("utf-8")

    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}?output_format=pcm_44100",
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

        with sd.RawOutputStream(samplerate=44100, channels=1, dtype="int16") as stream:
            stream.write(pcm)
        return True
    except (HTTPError, URLError, OSError, sd.PortAudioError) as exc:
        print(f"[TTS] ElevenLabs gagal, fallback ke suara lokal: {exc}")
        return False


def speak(text: str) -> None:
    text = str(text).strip()
    if not text:
        return

    print(f"JARVIS: {text}")
    if not _elevenlabs_speak(text):
        _fallback_speak(text)
