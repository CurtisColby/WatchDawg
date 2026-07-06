"""
Web Test UI Router.

Serves the browser-based interface at the root URL (/).

Reads the page from app/templates/index.html on every request,
so UI deploys are just a file copy + container restart.
"""

import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web_ui"])

# Path to the templates directory (app/templates/)
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


@router.get("/", response_class=HTMLResponse)
async def serve_web_ui(request: Request):
    """Serve the WatchDawg web interface."""
    template_path = os.path.join(TEMPLATE_DIR, "index.html")
    with open(template_path, "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)
