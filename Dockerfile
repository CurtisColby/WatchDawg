FROM python:3.11-slim

# Install system dependencies for yt-dlp and media processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (overridden at runtime by volume mount)
COPY app/ ./app/

# Create necessary directories
RUN mkdir -p /app/data /music_videos

EXPOSE 6868

# Run with uvicorn — single worker for SQLite safety
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6868", "--reload"]
