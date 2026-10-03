"""JARVIS local desktop agent.

Microphone -> Gemini speech-to-text/reasoning -> tool calling -> PC action -> voice.
The PC tool set is intentionally allowlisted instead of exposing arbitrary shell access.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import wave
import webbrowser
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

import sounddevice as sd
import speech_recognition as sr
from dotenv import load_dotenv
from google import genai
from google.genai import types

from jarvis_tts import speak

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
STT_MODEL = os.getenv("GEMINI_STT_MODEL", MODEL)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SAMPLE_RATE = 16000
RECORD_SECONDS = 6
MIN_RECORD_SECONDS = 0.35
SILENCE_SECONDS = 0.65
START_TIMEOUT_SECONDS = 0
ENERGY_THRESHOLD = 120


SYSTEM_PROMPT = """
You are JARVIS, a local Windows desktop AI assistant.

Speak natural casual Indonesian by default. Use gue/lu naturally and address the
user as "Sir" occasionally, not every sentence.

You have access to a small set of local PC tools. Decide yourself when a tool is
needed. Do not claim an action happened unless its tool result says it succeeded.
After a tool runs, briefly explain the result to the user.

Available capabilities include opening approved applications, websites, folders,
and reading basic computer status.

Do not invent access to files or system state. Do not execute destructive,
credential-stealing, surveillance, financial, or otherwise dangerous actions.
For actions that could delete data, shut down the machine, install unknown
software, or change security settings, do not execute them automatically.

Keep spoken answers concise. If the user asks a normal knowledge question,
answer it directly without calling a PC tool.
"""

APPS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "chrome": ["cmd", "/c", "start", "", "chrome"],
    "vscode": ["cmd", "/c", "start", "", "code"],
    "explorer": ["explorer.exe"],
    "task manager": ["taskmgr.exe"],
    "settings": ["cmd", "/c", "start", "", "ms-settings:"],
}

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
}

FOLDERS = {
    "home": Path.home(),
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
}

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": "Open an approved Windows application.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": [
                        "notepad", "calculator", "chrome", "vscode",
                        "explorer", "task manager", "settings",
                    ],
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_web",
        "description": "Search the web using Google for the user's requested topic. Use this when the user asks to search, find, look up, or research something online.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_site",
        "description": "Open an approved website in the default browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": ["youtube", "google", "github", "chatgpt"],
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "open_folder",
        "description": "Open a common user folder in Windows Explorer.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": ["home", "desktop", "documents", "downloads"],
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "pc_status",
        "description": "Read basic non-sensitive computer status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOLS = [types.Tool(function_declarations=TOOL_DECLARATIONS)]


def open_app(name: str) -> str:
    if name not in APPS:
        return f"Aplikasi {name} belum tersedia."
    try:
        subprocess.Popen(APPS[name], shell=False)
        return f"Berhasil membuka {name}."
    except OSError as exc:
        return f"Gagal membuka {name}: {exc}"


def search_web(query: str) -> str:
    query = str(query).strip()
    if not query:
        return "Query pencarian kosong."
    try:
        url = "https://www.google.com/search?q=" + quote_plus(query)
        opened = webbrowser.open(url, new=2)
        return f"Berhasil mencari {query} di Google." if opened else "Browser menolak membuka hasil pencarian."
    except OSError as exc:
        return f"Gagal melakukan pencarian: {exc}"


def open_site(name: str) -> str:
    if name not in SITES:
        return f"Website {name} belum tersedia."
    try:
        opened = webbrowser.open(SITES[name], new=2)
        return f"Berhasil membuka {name}." if opened else f"Browser menolak membuka {name}."
    except OSError as exc:
        return f"Gagal membuka {name}: {exc}"


def open_folder(name: str) -> str:
    folder = FOLDERS.get(name)
    if folder is None:
        return f"Folder {name} belum tersedia."
    if not folder.exists():
        return f"Folder {name} tidak ditemukan."
    try:
        os.startfile(str(folder))
        return f"Berhasil membuka folder {name}."
    except OSError as exc:
        return f"Gagal membuka folder {name}: {exc}"


def pc_status() -> str:
    free_gb = shutil.disk_usage(Path.home()).free / (1024 ** 3)
    return (
        f"OS: {platform.system()} {platform.release()}; "
        f"komputer: {platform.node()}; "
        f"Python: {platform.python_version()}; "
        f"ruang kosong: {free_gb:.1f} GB."
    )


def run_tool(name: str, arguments: dict[str, Any]) -> str:
    if name == "open_app":
        return open_app(str(arguments["name"]))
    if name == "search_web":
        return search_web(str(arguments["query"]))
    if name == "open_site":
        return open_site(str(arguments["name"]))
    if name == "open_folder":
        return open_folder(str(arguments["name"]))
    if name == "pc_status":
        return pc_status()
    return f"Tool {name} tidak dikenal."


def _gemini_generate(client: genai.Client, *, model: str, contents: Any, config: Any):
    last_error = None
    for attempt in range(3):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            last_error = exc
            message = str(exc).upper()
            if "503" not in message and "UNAVAILABLE" not in message and "429" not in message:
                raise
            wait = 2 * (attempt + 1)
            print(f"[GEMINI] Layanan sibuk ({attempt + 1}/3), retry {wait} detik...")
            time.sleep(wait)
    raise last_error


def ask_agent(client: genai.Client, user_text: str) -> str:
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=user_text)],
        )
    ]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,
        temperature=0.7,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for _ in range(5):
        response = _gemini_generate(client, model=MODEL, contents=contents, config=config)

        calls = response.function_calls or []
        if not calls:
            return (response.text or "").strip()

        contents.append(response.candidates[0].content)

        for call in calls:
            try:
                arguments = dict(call.args or {})
                result = run_tool(call.name, arguments)
            except Exception as exc:
                result = f"Tool error: {exc}"

            print(f"[TOOL] {call.name} -> {result}")
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=call.name,
                            response={"result": result},
                            id=getattr(call, "id", None),
                        )
                    ],
                )
            )

    return "Saya berhenti setelah beberapa langkah tool agar tidak masuk loop."


def record_audio(path: Path) -> None:
    print("[MIC] Mendengarkan...")
    block_size = 1600
    max_blocks = int(RECORD_SECONDS * SAMPLE_RATE / block_size)
    min_blocks = max(1, int(MIN_RECORD_SECONDS * SAMPLE_RATE / block_size))
    silence_blocks = max(1, int(SILENCE_SECONDS * SAMPLE_RATE / block_size))
    start_timeout_blocks = max(1, int(START_TIMEOUT_SECONDS * SAMPLE_RATE / block_size))

    chunks = []
    started = False
    quiet_count = 0

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=block_size,
    ) as stream:
        # Wait indefinitely for the first real speech signal.
        # START_TIMEOUT_SECONDS=0 means "no timeout", not "wait one block".
        while not started:
            data, _ = stream.read(block_size)
            chunk = data.copy()
            energy = float(abs(chunk).mean())

            if energy >= ENERGY_THRESHOLD:
                started = True
                chunks.append(chunk)
                quiet_count = 0
                print("[MIC] Suara terdeteksi.")

        # Once speech starts, record until silence or the 6-second cap.
        remaining_blocks = max_blocks - 1
        for _ in range(max(0, remaining_blocks)):
            data, _ = stream.read(block_size)
            chunk = data.copy()
            energy = float(abs(chunk).mean())
            chunks.append(chunk)

            if energy < ENERGY_THRESHOLD:
                quiet_count += 1
                if len(chunks) >= min_blocks and quiet_count >= silence_blocks:
                    break
            else:
                quiet_count = 0

    if not chunks:
        raise RuntimeError("Tidak ada suara terdeteksi.")

    recording = __import__("numpy").concatenate(chunks, axis=0)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(recording.tobytes())


def try_direct_command(text: str) -> str | None:
    normalized = " ".join(text.lower().strip().split())
    prefixes = ("tolong ", "jarvis ", "bisa ")

    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()

    app_aliases = {
        "chrome": "chrome",
        "google chrome": "chrome",
        "notepad": "notepad",
        "kalkulator": "calculator",
        "calculator": "calculator",
        "explorer": "explorer",
        "task manager": "task manager",
        "vscode": "vscode",
        "vs code": "vscode",
        "pengaturan": "settings",
        "settings": "settings",
    }

    if normalized.startswith("buka ") and normalized[5:].strip() in app_aliases:
        app = app_aliases[normalized[5:].strip()]
        result = open_app(app)
        print(f"[DIRECT] open_app -> {result}")
        return result

    search_prefixes = (
        "cari tentang ",
        "cari ",
        "search tentang ",
        "search ",
        "google tentang ",
        "google ",
        "buka chrome dan cari tentang ",
        "buka chrome lalu cari tentang ",
        "buka chrome terus cari tentang ",
        "buka chrome dan cari ",
        "buka chrome lalu cari ",
        "buka chrome terus cari ",
    )

    for prefix in search_prefixes:
        if normalized.startswith(prefix):
            query = normalized[len(prefix):].strip()
            if query:
                result = search_web(query)
                print(f"[DIRECT] search_web -> {result}")
                return result

    return None


def transcribe(client: genai.Client, path: Path) -> str:
    # Local Google Speech first: fast and independent of Gemini availability.
    # Gemini remains the fallback for cases where Google cannot recognize the audio.
    recognizer = sr.Recognizer()
    with sr.AudioFile(str(path)) as source:
        audio = recognizer.record(source)

    try:
        text = recognizer.recognize_google(audio, language="id-ID")
        text = text.strip()
        if text:
            print(f"[STT] Google: {text}")
            return text
    except sr.UnknownValueError:
        pass
    except sr.RequestError as exc:
        print(f"[STT] Google Speech gagal: {exc}")

    try:
        audio_bytes = path.read_bytes()
        response = _gemini_generate(
            client,
            model=STT_MODEL,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                types.Part(
                    text=(
                        "Transkripsikan audio ini ke teks bahasa Indonesia. "
                        "Tulis hanya apa yang diucapkan, tanpa penjelasan tambahan. "
                        "Kalau tidak ada ucapan yang jelas, balas kosong."
                    )
                ),
            ],
            config=types.GenerateContentConfig(temperature=0),
        )
        return (response.text or "").strip()
    except Exception as exc:
        print(f"[STT] Gemini fallback gagal: {exc}")
        return ""


def main() -> None:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY belum diatur.")
        return

    client = genai.Client(api_key=GEMINI_API_KEY)
    speak("Sistem aktif, Sir. Saya siap mendengarkan.")
    audio_path = Path(__file__).resolve().with_name(".jarvis_input.wav")

    try:
        while True:
            try:
                record_audio(audio_path)
                heard = transcribe(client, audio_path)
            except (OSError, sd.PortAudioError) as exc:
                print(f"[MIC] {exc}")
                speak("Mikrofon tidak bisa diakses. Periksa perangkat audio Windows.")
                time.sleep(2)
                continue
            except Exception as exc:
                print(f"[STT] {exc}")
                speak("Pengenalan suara gagal. Periksa koneksi dan Gemini API key.")
                time.sleep(2)
                continue

            if not heard:
                continue

            print(f"Sir: {heard}")
            normalized = heard.lower().strip()

            if normalized in {
                "shutdown jarvis",
                "matikan jarvis",
                "berhenti mendengarkan",
                "matikan mode suara",
                "stop jarvis",
            }:
                speak("Mode suara dihentikan, Sir.")
                break

            direct_reply = try_direct_command(heard)
            if direct_reply is not None:
                speak(direct_reply)
                continue

            try:
                reply = ask_agent(client, heard)
                if reply:
                    speak(reply)
            except Exception as exc:
                print(f"[AGENT] {exc}")
                speak("Saya gagal memproses permintaan itu, Sir. Periksa log terminal.")

    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
