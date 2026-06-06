"""
Web Test UI Router.

Serves the browser-based test interface at the root URL (/).
This replaces the JSON root endpoint and serves the full HTML
control deck so you can test all API functionality from a browser.

In production (Android TV phase), this could be disabled or replaced
with an admin panel.
"""

import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web_ui"])

# Path to the templates directory
TEMPLATE_DIR = os.path.dirname(os.path.dirname(__file__))


@router.get("/", response_class=HTMLResponse)
async def serve_web_ui(request: Request):
    """Serve the WatchDawg web test interface."""
    template_path = os.path.join(TEMPLATE_DIR, "index.html")
    with open(template_path, "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)
