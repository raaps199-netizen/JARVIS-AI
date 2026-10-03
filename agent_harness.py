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
import pyautogui
import pyperclip
from urllib.parse import quote_plus
from pathlib import Path
from typing import Any

import sounddevice as sd
import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

from jarvis_tts import speak

load_dotenv()

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SAMPLE_RATE = 16000
RECORD_SECONDS = 6
MIN_RECORD_SECONDS = 0.35
SILENCE_SECONDS = 0.65
UI_DELAY_SECONDS = 0.8
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
    "word": ["cmd", "/c", "start", "", "winword"],
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
    {"type":"function","function":{"name":"open_app","description":"Open an approved Windows application.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["notepad","calculator","chrome","vscode","word","explorer","task manager","settings"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"search_web","description":"Search Google for a requested topic.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"open_site","description":"Open an approved website.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["youtube","google","github","chatgpt"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"open_folder","description":"Open a common user folder.","parameters":{"type":"object","properties":{"name":{"type":"string","enum":["home","desktop","documents","downloads"]}},"required":["name"]}}},
    {"type":"function","function":{"name":"type_text","description":"Type exact text into the currently focused desktop application when explicitly requested.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}},
    {"type":"function","function":{"name":"press_key","description":"Press an allowlisted keyboard key or shortcut.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"pc_status","description":"Read basic non-sensitive computer status.","parameters":{"type":"object","properties":{}}}}
]

TOOLS = TOOL_DECLARATIONS


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
    if name == "type_text":
        return type_text(str(arguments["text"]))
    if name == "press_key":
        return press_key(str(arguments["key"]))
    if name == "pc_status":
        return pc_status()
    if name == "pc_status":
        return pc_status()
    return f"Tool {name} tidak dikenal."


def ask_agent(client: Groq, user_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    for _ in range(6):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.4,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return (message.content or "").strip()

        messages.append(message)

        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                result = run_tool(tool_call.function.name, arguments)
            except Exception as exc:
                result = f"Tool error: {exc}"

            print(f"[TOOL] {tool_call.function.name} -> {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

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
        "word": "word",
        "microsoft word": "word",
        "note": "notepad",
        "catatan": "notepad",
        "pengaturan": "settings",
        "settings": "settings",
    }

    if normalized.startswith("buka ") and normalized[5:].strip() in app_aliases:
        app = app_aliases[normalized[5:].strip()]
        result = open_app(app)
        print(f"[DIRECT] open_app -> {result}")
        return result

    # Handle common "buka X lalu/terus/dan cari Y" phrasing without Gemini.
    combined_search_markers = (" lalu cari ", " terus cari ", " dan cari ")
    if normalized.startswith("buka ") and any(marker in normalized for marker in combined_search_markers):
        for marker in combined_search_markers:
            if marker in normalized:
                app_part, query = normalized[5:].split(marker, 1)
                app = app_aliases.get(app_part.strip())
                query = query.strip()
                if app == "chrome" and query:
                    open_result = open_app("chrome")
                    search_result = search_web(query)
                    result = f"{open_result} {search_result}"
                    print(f"[DIRECT] chrome search -> {result}")
                    return result

    typing_prefixes = ("ketik ", "tulis ", "ketikkan ")
    for prefix in typing_prefixes:
        if normalized.startswith(prefix):
            text = normalized[len(prefix):].strip()
            if text:
                result = type_text(text)
                print(f"[DIRECT] type_text -> {result}")
                return result

    action_markers = (" dan ketik ", " lalu ketik ", " terus ketik ", " dan tulis ", " lalu tulis ")
    if normalized.startswith("buka "):
        for marker in action_markers:
            if marker in normalized:
                app_part, text = normalized[5:].split(marker, 1)
                app = app_aliases.get(app_part.strip())
                text = text.strip()
                if app and text:
                    open_result = open_app(app)
                    time.sleep(UI_DELAY_SECONDS)
                    type_result = type_text(text)
                    result = f"{open_result} {type_result}"
                    print(f"[DIRECT] open + type -> {result}")
                    return result

    key_aliases = {
        "enter": "enter", "tekan enter": "enter", "escape": "esc", "esc": "esc",
        "tab": "tab", "hapus": "backspace", "backspace": "backspace",
        "ctrl s": "ctrl+s", "ctrl n": "ctrl+n", "ctrl a": "ctrl+a",
        "ctrl c": "ctrl+c", "ctrl v": "ctrl+v", "ctrl z": "ctrl+z",
        "alt f4": "alt+f4",
    }
    if normalized in key_aliases:
        result = press_key(key_aliases[normalized])
        print(f"[DIRECT] press_key -> {result}")
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


def transcribe(client: Groq, path: Path) -> str:
    try:
        with open(path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(path.name, audio_file.read()),
                model=STT_MODEL,
                language="id",
                response_format="text",
                temperature=0,
            )
        text = str(transcription).strip()
        if text:
            print(f"[STT] Groq: {text}")
        return text
    except Exception as exc:
        print(f"[STT] Groq gagal: {exc}")
        return ""


def main() -> None:
    if not GROQ_API_KEY:
        print("GROQ_API_KEY belum diatur.")
        return

    client = Groq(api_key=GROQ_API_KEY)
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
                speak("Pengenalan suara gagal. Periksa koneksi dan GROQ API key.")
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
