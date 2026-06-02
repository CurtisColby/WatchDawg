FROM python:3.11-slim

# Install system dependencies for yt-dlp and media processing.
# Node.js 22 from NodeSource — required by yt-dlp EJS challenge solver.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp EJS challenge solver scripts (PyPI package).
# This provides the JavaScript challenge scripts yt-dlp needs to access
# YouTube's full format list including 1080p streams.
RUN pip install --no-cache-dir yt-dlp-ejs

# Configure yt-dlp to use Node.js runtime globally.
# Without this config, yt-dlp won't know to use the installed Node.js.
RUN mkdir -p /etc && echo '--js-runtimes node' > /etc/yt-dlp.conf

# Copy application code (overridden at runtime by volume mount)
COPY app/ ./app/

# Create necessary directories
RUN mkdir -p /app/data /music_videos

EXPOSE 6868

# Run with uvicorn — single worker for SQLite safety
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6868", "--reload"]
