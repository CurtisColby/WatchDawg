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

    # Vimeo cookie health (Session 68) — mandatory account cookies since
    # Vimeo killed anonymous extraction on 2026-07-20. Same import-inside
    # pattern: a resolver import problem can never take /health down.
    try:
        from app.services.resolver import get_vimeo_cookie_status
        vimeo_cookies = get_vimeo_cookie_status()
    except Exception:
        vimeo_cookies = {"state": "unknown"}

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "service": "watchdawg-backend",
        "youtube_cookies": youtube_cookies,
        "vimeo_cookies": vimeo_cookies,
    }


@router.get("/health/scheduler")
async def scheduler_status():
    """
    Scheduler dashboard feed. (Session 63)

    Returns every background job with its next run time, running-now flag,
    last run start/end/duration, a counts-only result summary, last error,
    and lifetime run/error counts. Powers the Settings-page scheduler panel.

    Lock discipline: summaries contain counts only — never video or channel
    titles — because this endpoint is readable by locked (public) sessions.
    """
    try:
        from app.tasks.scheduler import get_scheduler_status
        return get_scheduler_status()
    except Exception as e:
        return {"scheduler_running": False, "jobs": [], "error": str(e)[:200]}


@router.post("/health/scheduler/run/{job_id}")
async def scheduler_run_now(job_id: str):
    """
    Trigger a background job to run immediately (the dashboard's Run Now
    button). The job's regular interval continues unchanged afterward.
    """
    from fastapi import HTTPException
    from app.tasks.scheduler import run_job_now, get_scheduler_status

    if not run_job_now(job_id):
        valid = [j["id"] for j in get_scheduler_status()["jobs"]]
        raise HTTPException(
            status_code=404,
            detail=f"No scheduled job named '{job_id}'. Valid jobs: {', '.join(valid)}",
        )
    return {"status": "triggered", "message": f"Job '{job_id}' will run within seconds."}
