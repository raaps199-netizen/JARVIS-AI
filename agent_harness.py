"""JARVIS local desktop agent.

Microphone -> speech recognition -> OpenAI reasoning/tool calling -> PC action -> voice.
The agent uses an allowlisted tool set instead of arbitrary shell execution.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

import time
import wave

import sounddevice as sd
from dotenv import load_dotenv
from openai import OpenAI

from jarvis_tts import speak

load_dotenv()

MODEL = os.getenv("JARVIS_MODEL", "gpt-6-luna")
TRANSCRIPTION_LANGUAGE = os.getenv("JARVIS_LANGUAGE", "id")
SAMPLE_RATE = 16000
RECORD_SECONDS = 6


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

TOOLS = [
    {
        "type": "function",
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
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
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
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
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
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "pc_status",
        "description": "Read basic non-sensitive computer status.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def open_app(name: str) -> str:
    if name not in APPS:
        return f"Aplikasi {name} belum tersedia."
    try:
        subprocess.Popen(APPS[name], shell=False)
        return f"Berhasil membuka {name}."
    except OSError as exc:
        return f"Gagal membuka {name}: {exc}"


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
    if name == "open_site":
        return open_site(str(arguments["name"]))
    if name == "open_folder":
        return open_folder(str(arguments["name"]))
    if name == "pc_status":
        return pc_status()
    return f"Tool {name} tidak dikenal."


def ask_agent(client: OpenAI, user_text: str) -> str:
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_text,
        tools=TOOLS,
    )

    for _ in range(5):
        calls = [
            item for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]
        if not calls:
            return response.output_text.strip()

        tool_outputs = []
        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")
                result = run_tool(call.name, arguments)
            except Exception as exc:
                result = f"Tool error: {exc}"

            print(f"[TOOL] {call.name} -> {result}")
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": result,
            })

        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            previous_response_id=response.id,
            input=tool_outputs,
            tools=TOOLS,
        )

    return "Saya berhenti setelah beberapa langkah tool agar tidak masuk loop."


def record_audio(path: Path) -> None:
    print(f"[MIC] Merekam {RECORD_SECONDS} detik...")
    recording = sd.rec(
        int(RECORD_SECONDS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(recording.tobytes())


def transcribe(client: OpenAI, path: Path) -> str:
    with path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(
            model=os.getenv("JARVIS_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
            file=audio_file,
            language=TRANSCRIPTION_LANGUAGE,
        )
    return result.text.strip()


def main() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY belum diatur.")
        return

    client = OpenAI(api_key=api_key)
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
                speak("Pengenalan suara gagal. Periksa koneksi dan API key.")
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
