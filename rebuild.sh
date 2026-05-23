#!/bin/bash
# WatchDawg Image Rebuild Script
# Run from: ~/watchdawg-backend/
# 
# What this does:
#   1. Syncs all live container source files to ~/watchdawg-backend/app/
#   2. Copies fixed files from ~/Downloads/ into the source tree
#   3. Replaces requirements.txt, Dockerfile, docker-compose.yml
#   4. Rebuilds the Docker image and restarts the container
#
# Data safety:
#   - Database (./data/) is volume-mounted — survives rebuild untouched
#   - NAS downloads (/media/colby/NAS1/WatchDawg) are external — untouched
#   - cookies.txt (./config/) is volume-mounted — untouched

set -e  # Exit on any error
cd ~/watchdawg-backend

echo "=========================================="
echo " WatchDawg Rebuild"
echo "=========================================="

# ------------------------------------------
# STEP 1: Sync live container -> source dir
# ------------------------------------------
echo ""
echo "[1/5] Syncing live container source to ./app/ ..."

mkdir -p app/routers app/services app/tasks app/providers app/templates

# Core app files
docker cp watchdawg-backend:/app/app/main.py        app/main.py
docker cp watchdawg-backend:/app/app/__init__.py    app/__init__.py
docker cp watchdawg-backend:/app/app/config.py      app/config.py
docker cp watchdawg-backend:/app/app/database.py    app/database.py
docker cp watchdawg-backend:/app/app/models.py      app/models.py
docker cp watchdawg-backend:/app/app/encryption.py  app/encryption.py
docker cp watchdawg-backend:/app/app/hashing.py     app/hashing.py

# Routers
docker cp watchdawg-backend:/app/app/routers/__init__.py  app/routers/__init__.py
docker cp watchdawg-backend:/app/app/routers/channel.py   app/routers/channel.py
docker cp watchdawg-backend:/app/app/routers/favorite.py  app/routers/favorite.py
docker cp watchdawg-backend:/app/app/routers/feed.py      app/routers/feed.py
docker cp watchdawg-backend:/app/app/routers/health.py    app/routers/health.py
docker cp watchdawg-backend:/app/app/routers/library.py   app/routers/library.py
docker cp watchdawg-backend:/app/app/routers/proxy.py     app/routers/proxy.py
docker cp watchdawg-backend:/app/app/routers/resolve.py   app/routers/resolve.py
docker cp watchdawg-backend:/app/app/routers/skip.py      app/routers/skip.py
docker cp watchdawg-backend:/app/app/routers/web_ui.py    app/routers/web_ui.py

# Services
docker cp watchdawg-backend:/app/app/services/__init__.py  app/services/__init__.py
docker cp watchdawg-backend:/app/app/services/resolver.py  app/services/resolver.py
docker cp watchdawg-backend:/app/app/services/scraper.py   app/services/scraper.py

# Tasks
docker cp watchdawg-backend:/app/app/tasks/__init__.py    app/tasks/__init__.py
docker cp watchdawg-backend:/app/app/tasks/scheduler.py   app/tasks/scheduler.py

# Providers
docker cp watchdawg-backend:/app/app/providers/__init__.py  app/providers/__init__.py
docker cp watchdawg-backend:/app/app/providers/base.py      app/providers/base.py
docker cp watchdawg-backend:/app/app/providers/reddit.py    app/providers/reddit.py
docker cp watchdawg-backend:/app/app/providers/playlist.py  app/providers/playlist.py
docker cp watchdawg-backend:/app/app/providers/vimeo_rss.py app/providers/vimeo_rss.py

# Templates
docker cp watchdawg-backend:/app/app/templates/index.html  app/templates/index.html

echo "    Done — all container files synced to ./app/"

# ------------------------------------------
# STEP 2: Overlay fixed files from Downloads
# ------------------------------------------
echo ""
echo "[2/5] Overlaying fixed files from ~/Downloads/ ..."

# These are the files fixed this session — they are already deployed
# to the running container via docker cp, so syncing from the container
# above already captured them. This step is a safety overlay in case
# any Downloads copy is newer than what's in the container.

[ -f ~/Downloads/channel.py ]   && cp ~/Downloads/channel.py   app/routers/channel.py   && echo "    Overlaid channel.py"
[ -f ~/Downloads/scraper.py ]   && cp ~/Downloads/scraper.py   app/services/scraper.py  && echo "    Overlaid scraper.py"
[ -f ~/Downloads/vimeo_rss.py ] && cp ~/Downloads/vimeo_rss.py app/providers/vimeo_rss.py && echo "    Overlaid vimeo_rss.py"

echo "    Done."

# ------------------------------------------
# STEP 3: Replace build files
# ------------------------------------------
echo ""
echo "[3/5] Replacing requirements.txt, Dockerfile, docker-compose.yml ..."

cp ~/Downloads/requirements.txt  ./requirements.txt
cp ~/Downloads/Dockerfile        ./Dockerfile
cp ~/Downloads/docker-compose.yml ./docker-compose.yml

echo "    Done."

# ------------------------------------------
# STEP 4: Rebuild image
# ------------------------------------------
echo ""
echo "[4/5] Building new Docker image (this takes 2-4 minutes) ..."
docker compose build --no-cache

echo "    Image built."

# ------------------------------------------
# STEP 5: Restart with new image
# ------------------------------------------
echo ""
echo "[5/5] Restarting container with new image ..."
docker compose up -d

echo ""
echo "=========================================="
echo " Rebuild complete!"
echo " - yt-dlp and curl-cffi are now baked in"
echo " - Source code is volume-mounted at ./app/"
echo " - Future deploys: copy file to ~/watchdawg-backend/app/... restart"
echo "=========================================="
echo ""

# Quick health check
sleep 5
curl -s http://localhost:6868/health | python3 -m json.tool | grep -E '"status"|"database"'
