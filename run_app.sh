#!/bin/sh

cd "$(dirname "$0")" || exit 1
./venv/bin/streamlit run app.py
