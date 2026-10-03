import os
import tempfile
import wave

import sounddevice as sd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SAMPLE_RATE = 16000
CHANNELS = 1
RECORD_SECONDS = 5
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


def record_audio(seconds=RECORD_SECONDS):
    print(f"🎙️ Ngomong sekarang... ({seconds} detik)")

    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
    )
    sd.wait()

    return audio


def save_wav(audio, path):
    with wave.open(path, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(audio.tobytes())


def transcribe_audio(client, path):
    with open(path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL,
            file=audio_file,
            language="id",
        )

    return transcript.text.strip()


def main():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("OPENAI_API_KEY belum diatur.")
        return

    client = OpenAI(api_key=api_key)

    print("================================")
    print(" JARVIS - VOICE INPUT TEST")
    print(" Ctrl+C untuk berhenti.")
    print("================================")

    try:
        while True:
            input("\nTekan ENTER untuk mulai merekam...")

            audio = record_audio()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
                temp_path = temp.name

            try:
                save_wav(audio, temp_path)
                text = transcribe_audio(client, temp_path)

                if text:
                    print(f"📝 Lu: {text}")
                else:
                    print("JARVIS: Gue nggak nangkep apa-apa.")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

    except KeyboardInterrupt:
        print("\nJARVIS: Voice input dihentikan.")


if __name__ == "__main__":
    main()
