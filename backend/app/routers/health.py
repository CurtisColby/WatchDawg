"""
Health check endpoint.

Used by Docker healthchecks, monitoring, and the web test UI
to confirm the backend is alive and the database is accessible.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db_session

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db_session)):
    """Return system health status including database connectivity."""
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    # YouTube cookie health (Session 53) — lets the web UI flag stale
    # cookies instead of the problem hiding in log lines. Import here
    # (not at module level) so a resolver import problem can never take
    # the health endpoint down with it.
    try:
        from app.services.resolver import get_youtube_cookie_status
        youtube_cookies = get_youtube_cookie_status()
    except Exception:
        youtube_cookies = {"state": "unknown"}

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "service": "watchdawg-backend",
        "youtube_cookies": youtube_cookies,
    }
