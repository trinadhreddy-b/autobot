#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# start.sh — Launch the chatbot platform locally
# Usage:  bash start.sh
# ────────────────────────────────────────────────────────────
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Check for .env
if [ ! -f ".env" ]; then
  echo "⚠️  .env not found — copying .env.example"
  cp .env.example .env
  echo "✏️  Please edit .env and add at least one API key, then re-run."
  exit 1
fi

# Check for venv
if [ ! -d "venv" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv venv
fi

# Activate venv
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate

# Install / update deps
echo "📦 Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Create required directories
mkdir -p database chroma_db data/uploads logs

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖  ChatBot Platform starting..."
echo "  🌐  Dashboard:   http://localhost:8000"
echo "  📖  API Docs:    http://localhost:8000/api/docs"
echo "  🎭  Widget Demo: http://localhost:8000/widget-demo"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
