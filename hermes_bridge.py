"""Small bridge from the JARVIS voice loop to Hermes Agent.

Hermes is used as the agentic desktop brain. The bridge intentionally enables only
the computer_use toolset, not Hermes terminal/file/code-execution tools.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional


HERMES_TOOLSETS = os.getenv("HERMES_TOOLSETS", "computer_use")
HERMES_MAX_TURNS = os.getenv("HERMES_MAX_TURNS", "40")

SYSTEM_CONTEXT = """You are the agentic desktop core of JARVIS on a Windows PC.
Always respond in natural English, even when the user speaks Indonesian.
Use the computer_use tool to actually perform desktop tasks, not merely describe
what the user should click.

Work on the same Windows desktop the user is using. Capture/inspect the screen
before visual actions, perform the requested action, and verify the result.
Continue through multi-step tasks until the requested end state is reached.

Safety rules:
- Only use the tools made available to this session.
- Do not use terminal, shell, arbitrary code execution, credential extraction,
  password/cookie theft, hidden surveillance, destructive file deletion,
  security-setting changes, financial actions, or other dangerous actions.
- Do not shut down or restart Windows.
- For ambiguous or high-impact actions, stop and explain what is unclear.
- Never claim success unless you verified the action.
"""

def hermes_available() -> bool:
    return shutil.which("hermes") is not None


def ask_hermes(user_text: str) -> Optional[str]:
    if not hermes_available():
        return None

    prompt = f"{SYSTEM_CONTEXT}\n\nUser request:\n{user_text.strip()}"

    cmd = [
        "hermes",
        "chat",
        "--oneshot",
        "--query",
        prompt,
        "--toolsets",
        HERMES_TOOLSETS,
        "--max-turns",
        HERMES_MAX_TURNS,
        "--format",
        "stream-json",
    ]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Hermes could not be started: {exc}"

    final_text = ""
    errors = []

    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "text":
            final_text += str(event.get("text", ""))
        elif event.get("type") == "result":
            final_text = str(event.get("text") or final_text)
            if event.get("exit_code", 0) != 0:
                errors.append(str(event.get("error") or "Hermes exited with an error."))

    if completed.returncode != 0 and not final_text:
        stderr = completed.stderr.strip()
        return f"Hermes failed: {stderr or 'unknown error'}"

    if errors and not final_text:
        return errors[-1]

    return final_text.strip() or "Hermes completed the desktop task without a final text response."
