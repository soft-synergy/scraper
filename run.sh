#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=== Niche Website Scraper ==="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Check for Anthropic API key (required for AI email generation)
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  ANTHROPIC_API_KEY not set. AI email generation will not work."
    echo "   Set it with: export ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
fi

echo ""
echo "Starting server at http://localhost:8000"
echo "Press Ctrl+C to stop"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
