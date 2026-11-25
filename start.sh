#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
if ! python -c 'import streamlit,pypdf,sklearn,requests' 2>/dev/null; then
  pip install -r requirements.txt
fi
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
