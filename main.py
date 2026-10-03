"""
JARVIS-AI
Initial local prototype.

This file will eventually connect:
microphone -> speech-to-text -> AI -> text-to-speech -> speaker
"""

import os
from dotenv import load_dotenv

load_dotenv()


def main():
    print("JARVIS is online.")
    print("Voice input/output will be connected in the next stage.")

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured yet.")


if __name__ == "__main__":
    main()
