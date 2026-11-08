#!/usr/bin/env zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
OLLAMA_DIR="$PROJECT_DIR/.local_ollama"
OLLAMA_BIN="$OLLAMA_DIR/ollama"
export OLLAMA_MODELS="$OLLAMA_DIR/models"

MODEL_TO_PULL="${1:-qwen2.5:7b}"

# Initialize Python virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install colorama prettytable rich prompt_toolkit tenacity pypdf

# Download and unpack portable Ollama binary
mkdir -p "$OLLAMA_DIR" "$OLLAMA_MODELS"

if [ ! -f "$OLLAMA_BIN" ]; then
    echo "Downloading Ollama binary into $OLLAMA_DIR..."
    curl -L "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz" -o "$OLLAMA_DIR/ollama-darwin.tgz"
    tar -xzf "$OLLAMA_DIR/ollama-darwin.tgz" -C "$OLLAMA_DIR"
    rm -f "$OLLAMA_DIR/ollama-darwin.tgz"
    chmod +x "$OLLAMA_BIN"
    echo "Ollama unpacked at: $OLLAMA_BIN"
else
    echo "Ollama binary already present at: $OLLAMA_BIN"
fi

# Start local Ollama daemon and pull requested model
if ! pgrep -f "$OLLAMA_BIN serve" > /dev/null 2>&1; then
    OLLAMA_MODELS="$OLLAMA_MODELS" "$OLLAMA_BIN" serve > "$OLLAMA_DIR/ollama_server.log" 2>&1 &
    sleep 3
fi

OLLAMA_MODELS="$OLLAMA_MODELS" "$OLLAMA_BIN" pull "$MODEL_TO_PULL"

echo "Environment setup complete:"
echo "- Python environment: $VENV_DIR"
echo "- Ollama directory:   $OLLAMA_DIR"
echo "- To run the app:     ./run_isolated_app.sh"

