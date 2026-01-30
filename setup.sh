#!/bin/bash

echo "🚀 Setting up YouTube Playlist Downloader..."

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null
then
    echo "❌ Error: ffmpeg is not installed. Please install it first."
    echo "On Ubuntu/Debian: sudo apt update && sudo apt install ffmpeg"
    exit 1
fi

echo "✅ ffmpeg found."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements
echo "📥 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Initial setup of directories
mkdir -p downloads static

echo "✨ Setup complete! to start the server:"
echo "source venv/bin/activate"
echo "python3 app.py"
