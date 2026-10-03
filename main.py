"""
JARVIS-AI
Text + streaming web voice assistant.

Local text mode:
    python main.py

Web mode:
    Flask uses the top-level "app" object below.
"""

import json
import os

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
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


def stream_jarvis(client: OpenAI, user_message: str):
    stream = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
        stream=True,
    )

    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta


def transcribe_audio(client: OpenAI, audio_file):
    audio_bytes = audio_file.read()

    transcript = client.audio.transcriptions.create(
        model=TRANSCRIPTION_MODEL,
        file=(
            audio_file.filename or "jarvis-voice.webm",
            audio_bytes,
            audio_file.mimetype or "audio/webm",
        ),
        language="id",
    )
    return transcript.text.strip()


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


@app.post("/api/music/youtube-search")
def youtube_search():
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({
            "error": "YOUTUBE_API_KEY belum diatur di environment variables."
        }), 503

    data = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()
    if not query:
        return jsonify({"error": "Judul lagu kosong."}), 400

    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    params = urlencode({
        "part": "snippet",
        "type": "video",
        "videoCategoryId": "10",
        "q": query,
        "maxResults": 5,
        "order": "relevance",
        "videoEmbeddable": "true",
        "videoSyndicated": "true",
        "safeSearch": "strict",
        "key": api_key,
    })
    url = "https://www.googleapis.com/youtube/v3/search?" + params
    req = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = "YouTube Data API menolak permintaan."
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = body.get("error", {}).get("message", detail)
        except Exception:
            pass
        return jsonify({"error": detail}), 502
    except (URLError, TimeoutError) as error:
        return jsonify({"error": "Gagal menghubungi YouTube: " + str(error)}), 502

    videos = []
    for item in result.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        if video_id:
            videos.append({
                "id": video_id,
                "title": snippet.get("title", "Untitled"),
                "channel": snippet.get("channelTitle", "YouTube"),
            })

    return jsonify({"items": videos})


@app.post("/api/tts")
def text_to_speech():
    """Generate speech with ElevenLabs; the API key stays server-side."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return jsonify({"error": "ELEVENLABS_API_KEY belum diatur di Vercel."}), 503

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "Teks suara kosong."}), 400
    if len(text) > 1200:
        return jsonify({"error": "Teks terlalu panjang untuk satu permintaan TTS."}), 400

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.78,
            "style": 0.2,
            "use_speaker_boost": True
        }
    }).encode("utf-8")
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    req = Request(
        "https://api.elevenlabs.io/v1/text-to-speech/" + voice_id + "?output_format=mp3_44100_128",
        data=payload,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        },
        method="POST"
    )
    try:
        with urlopen(req, timeout=20) as response:
            audio_bytes = response.read()
        return Response(audio_bytes, mimetype="audio/mpeg", headers={
            "Cache-Control": "no-store"
        })
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            detail = detail.get("detail", {}).get("message") or detail.get("detail", {}).get("status") or "ElevenLabs menolak permintaan."
        except Exception:
            detail = "ElevenLabs menolak permintaan."
        return jsonify({"error": detail}), 502
    except (URLError, TimeoutError):
        return jsonify({"error": "Tidak bisa menghubungi layanan ElevenLabs."}), 502


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


@app.post("/api/chat/stream")
def chat_stream():
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

    def generate():
        try:
            for delta in stream_jarvis(client, message):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as error:
            yield f"data: {json.dumps({'error': str(error)}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
