"""One-command setup + launch for the Naphora Clip Pipeline.

Run me:
    python run.py

What this script does:
  1. Checks ffmpeg is installed (if not, prints install hint and exits).
  2. Creates a Python virtual environment in .venv (if not already there).
  3. Installs all Python dependencies (idempotent — fast on subsequent runs).
  4. Launches the Gradio app and opens http://127.0.0.1:7860 in your browser.

Prerequisites you must install ONCE before running this:
  - Python 3.10+ (python.org)
  - ffmpeg (winget install Gyan.FFmpeg | brew install ffmpeg | apt install ffmpeg)
  - A free Gemini API key from https://aistudio.google.com/apikey
    (You can paste it in the UI textbox after the app launches — no need to
    create a .env file.)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
import webbrowser
from pathlib import Path
from threading import Timer

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
APP = ROOT / "app.py"
REQS = ROOT / "requirements.txt"
URL = "http://127.0.0.1:7860"


def banner(msg: str) -> None:
    print(f"\n{'=' * 64}\n  {msg}\n{'=' * 64}")


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        print(
            "\n❌ ffmpeg is not installed or not on PATH.\n"
            "Install it once with one of:\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg\n"
            "Then re-run: python run.py\n"
        )
        sys.exit(1)
    print("✅ ffmpeg OK")


def check_python_version() -> None:
    if sys.version_info < (3, 10):
        print(
            f"\n❌ Python {sys.version_info.major}.{sys.version_info.minor} is too old. "
            "Install Python 3.10+ from python.org and re-run.\n"
        )
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} OK")


def ensure_venv() -> None:
    if not VENV.exists():
        print("Creating virtual environment in .venv ...")
        venv.create(VENV, with_pip=True)
        print("✅ .venv created")
    else:
        print("✅ .venv exists, reusing")


def install_deps() -> None:
    py = venv_python()
    if not py.exists():
        print(f"❌ Could not find venv Python at {py}")
        sys.exit(1)
    print("Upgrading pip ...")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"]
    )
    print("Installing dependencies (one-time, ~2 minutes on first run) ...")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "-r", str(REQS), "--quiet"]
    )
    print("✅ Dependencies installed")


def open_browser_after_delay(delay_sec: float = 4.0) -> None:
    """Open the browser shortly after launching, so Gradio has time to bind the port."""
    Timer(delay_sec, lambda: webbrowser.open(URL)).start()


def launch_app() -> int:
    py = venv_python()
    print(f"\n🚀 Launching the pipeline at {URL}")
    print("   (Browser will open automatically. Press Ctrl+C in this terminal to stop.)")
    open_browser_after_delay()
    # Run app.py with the venv python — inherit stdio so user sees Gradio output
    return subprocess.call([str(py), str(APP)], cwd=str(ROOT))


def main() -> int:
    banner("1/4  Checking prerequisites")
    check_python_version()
    check_ffmpeg()

    banner("2/4  Setting up Python virtual environment")
    ensure_venv()

    banner("3/4  Installing dependencies")
    install_deps()

    banner("4/4  Launching the Naphora Clip Pipeline")
    return launch_app()


if __name__ == "__main__":
    sys.exit(main())
