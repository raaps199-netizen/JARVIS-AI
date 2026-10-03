"""
JARVIS-AI
Text prototype + Flask web app.

Local text mode:
    python main.py

Web mode:
    Flask uses the top-level "app" object below.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("JARVIS_MODEL", "gpt-6-luna")
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"

SYSTEM_PROMPT = """
You are JARVIS, a personal AI assistant for an Indonesian student.

PERSONALITY AND COMMUNICATION:
- Speak in natural, casual Indonesian by default.
- Use "gue/lu" naturally when speaking Indonesian.
- Sound like a smart, loyal friend who is direct and practical, not like a corporate chatbot.
- Keep answers concise by default. Explain more when the topic actually needs it.
- You may use light sarcasm, dry humor, witty observations, and playful jabs about confusing situations.
- The humor must never become cruel, insulting, humiliating, or unsafe.
- Be genuinely supportive when the user is dealing with something serious or sensitive.
- Never pretend to have human feelings, a body, or personal experiences.
- Do not use overly formal phrases unless the situation requires them.
- Do not start replies with "Yeah" or "Of course".
- Do not use em dashes.
- Avoid repetitive filler and generic chatbot phrases.
- Do not end with unnecessary opt-in questions such as "Mau gue...?" or "Kalau mau, gue bisa...".
- When giving instructions, use short numbered steps or bullets when that makes them easier to follow.
- If the user is learning programming, explain what the code does instead of only giving code to copy.
- If the user makes an incorrect assumption, correct it directly and explain why.
- If information is uncertain, say so rather than inventing an answer.

STYLE:
- Match the user's language. Indonesian is the default, but use English when the user is speaking English or asking about English.
- Match the user's casual energy without becoming incoherent.
- Occasional slang such as "anjir", "njir", "cuy", or "wok" is acceptable when it fits naturally, but do not force it into every response.
- Use emojis sparingly and only when they add something.
- For technical problems, prioritize a clear diagnosis and the exact next action.
"""


def create_client() -> OpenAI:
    if not API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY belum diatur. Masukkan API key sebagai environment variable."
        )

    return OpenAI(api_key=API_KEY)


def ask_jarvis(client: OpenAI, user_message: str) -> str:
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
    )
    return response.output_text.strip()


def transcribe_audio(client: OpenAI, audio_file):
    # Flask gives us a FileStorage object. The OpenAI SDK expects bytes,
    # a file-like object, a PathLike, or a supported upload tuple.
    audio_bytes = audio_file.read()

    transcript = client.audio.transcriptions.create(
        model=TRANSCRIPTION_MODEL,
        file=(audio_file.filename or "jarvis-voice.webm", audio_bytes, audio_file.mimetype or "audio/webm"),
        language="id",
    )
    return transcript.text.strip()


# Vercel detects this top-level Flask app automatically.
app = Flask(__name__)

try:
    client = create_client()
    startup_error = None
except Exception as error:
    client = None
    startup_error = str(error)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify({
        "online": client is not None,
        "error": startup_error,
    })


@app.post("/api/chat")
def chat():
    global client, startup_error

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify({"error": "Pesan kosong."}), 400

    if client is None:
        try:
            client = create_client()
            startup_error = None
        except Exception as error:
            startup_error = str(error)
            return jsonify({"error": startup_error}), 500

    try:
        reply = ask_jarvis(client, message)
        return jsonify({"reply": reply})
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.post("/api/transcribe")
def transcribe():
    global client, startup_error

    if client is None:
        try:
            client = create_client()
            startup_error = None
        except Exception as error:
            startup_error = str(error)
            return jsonify({"error": startup_error}), 500

    audio = request.files.get("audio")
    if audio is None:
        return jsonify({"error": "File audio tidak ditemukan."}), 400

    try:
        transcript = transcribe_audio(client, audio)
        return jsonify({"text": transcript})
    except Exception as error:
        return jsonify({"error": str(error)}), 500


def main():
    print("================================")
    print(" JARVIS AI - TEXT PROTOTYPE")
    print(" Ketik 'keluar' untuk berhenti.")
    print("================================")

    client = create_client()

    while True:
        try:
            user_message = input("\nLu: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nJARVIS: Sampai nanti.")
            break

        if not user_message:
            continue

        if user_message.lower() in {"keluar", "exit", "quit"}:
            print("JARVIS: Sampai nanti.")
            break

        try:
            reply = ask_jarvis(client, user_message)
            print(f"JARVIS: {reply}")
        except Exception as error:
            print(f"JARVIS: Terjadi error: {error}")


if __name__ == "__main__":
    main()
