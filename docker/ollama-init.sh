#!/bin/sh
set -e

MODEL="${OLLAMA_MODEL:-qwen2.5-coder:7b}"

echo "[ollama-init] Starting Ollama server..."
ollama serve &
SERVER_PID=$!

echo "[ollama-init] Waiting for server to be ready..."
until ollama list > /dev/null 2>&1; do
  sleep 2
done
echo "[ollama-init] Server is up."

echo "[ollama-init] Pulling model: $MODEL"
ollama pull "$MODEL"
echo "[ollama-init] Model ready."

wait $SERVER_PID
