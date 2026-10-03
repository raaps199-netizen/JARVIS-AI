"""
JARVIS-AI
Stage 1: text conversation prototype.

Next stages:
microphone -> speech-to-text -> AI -> text-to-speech -> speaker
"""

import os

from dotenv import load_dotenv
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
            "OPENAI_API_KEY belum diatur. Buat file .env dari .env.example "
            "dan masukkan API key di sana."
        )

    return OpenAI(api_key=API_KEY)


def ask_jarvis(client: OpenAI, user_message: str) -> str:
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
    )
    return response.output_text.strip()


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
