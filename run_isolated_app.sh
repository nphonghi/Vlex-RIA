#!/usr/bin/env zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
OLLAMA_DIR="$PROJECT_DIR/.local_ollama"
OLLAMA_BIN="$OLLAMA_DIR/ollama"
export OLLAMA_MODELS="$OLLAMA_DIR/models"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment (.venv)..."
    python3 -m venv "$VENV_DIR"
fi

# Release port 7860 if occupied by a previous session
OLD_PIDS=$(lsof -ti :7860 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    echo "$OLD_PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# Start local Ollama daemon with the updated models path
if [ -x "$OLLAMA_BIN" ]; then
    # If Ollama was running with an old path (e.g. before renaming the folder), restart it
    if ! lsof -i :11434 > /dev/null 2>&1; then
        echo "Starting local Ollama server..."
        OLLAMA_MODELS="$OLLAMA_MODELS" "$OLLAMA_BIN" serve > "$OLLAMA_DIR/ollama_server.log" 2>&1 &
        sleep 2
    else
        # Check if running Ollama is healthy and responding
        echo "Ollama service detected on port 11434."
    fi
fi

"$VENV_DIR/bin/python3" "$PROJECT_DIR/app_ria.py" "$@"
