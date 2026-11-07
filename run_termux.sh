#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
python -m pip install -r requirements.txt
python main.py "$@"
