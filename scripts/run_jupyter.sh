#!/bin/bash
set -e

if [ ! -d "venv" ]; then
  echo "venv/ nicht gefunden. Bitte zuerst erstellen:"
  echo "  python3 -m venv venv"
  exit 1
fi

source venv/bin/activate
jupyter notebook
