# JARVIS-AI

Personal AI assistant prototype.

## Current architecture

Stage 1: browser dashboard -> Python server -> AI -> browser

Next stages:
- microphone -> speech-to-text
- AI conversation
- text-to-speech -> speaker
- wake word
- music search/playback

## Run locally

1. Create a .env file from .env.example.
2. Put your API key in .env. Never commit the key to GitHub.
3. Install dependencies:
   py -m pip install -r requirements.txt
4. Start the dashboard:
   py web.py
5. Open http://127.0.0.1:5000

The browser is only the control panel. The API key stays in the Python server.
