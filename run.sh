#!/usr/bin/env bash
# One-command launcher for macOS / Linux. Run with:
#   ./run.sh
# (chmod +x run.sh first if needed)
#
# Prereqs (install once):
#   - Python 3.10+ from python.org
#   - ffmpeg: brew install ffmpeg (macOS) | sudo apt install ffmpeg (Linux)
#   - Free Gemini API key from https://aistudio.google.com/apikey
#     (paste in the UI textbox when the app opens)
set -e
cd "$(dirname "$0")"
python3 run.py
