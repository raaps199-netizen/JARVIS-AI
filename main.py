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

SYSTEM_PROMPT = """
You are JARVIS, a personal AI assistant.
Speak naturally and concisely.
Be helpful, practical, and friendly.
The user is Indonesian, so Indonesian is the default language unless they use another language.
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
