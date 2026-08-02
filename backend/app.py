"""FastAPI entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, jobs as jobs_mod, settings
from .models import Job
from .routes import dashboard, danbooru, export, ingest, jobs as jobs_route, scenes, tags
from .routes import admin as admin_route
from .routes import builder as builder_route
from .routes import classify as classify_route
from .routes import decompose as decompose_route
from .routes import llm as llm_route
from .routes import presets as presets_route
from .routes import trends as trends_route


logger = logging.getLogger("tagforge")


def _fail_orphaned_jobs() -> int:
    """Mark jobs left 'pending'/'running' by a backend crash as errored.

    Jobs run in-process (BackgroundTasks), so a restart kills them; rows
    left running make SSE streams hang forever and permanently 409-block
    batch-apply. Startup happens before any new job can be created, so
    every such row is by definition orphaned."""
    now = datetime.utcnow()
    with db.session_scope() as s:
        orphans = s.exec(
            select(Job).where(Job.status.in_(["pending", "running"]))  # type: ignore[attr-defined]
        ).all()
        for job in orphans:
            job.status = "error"
            job.error = "backend restarted while this job was running"
            job.finished_at = now
            job.updated_at = now
            s.add(job)
        return len(orphans)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    db.init_db()
    orphaned = _fail_orphaned_jobs()
    if orphaned:
        logger.warning("marked %d interrupted job(s) as errored", orphaned)
    jobs_mod.bind_loop(asyncio.get_running_loop())
    logger.info("Tag Forge backend ready (db=%s)", settings.DB_PATH)
    yield


app = FastAPI(title="Tag Forge", version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # No "*": the dev server proxies /api same-origin and prod is served by
    # this app directly, so a wildcard would only let arbitrary websites the
    # user is browsing read responses from this unauthenticated local API.
    allow_origins=[settings.DEV_FRONTEND_ORIGIN, "http://127.0.0.1:9300"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last = outermost, so a rebound Host is rejected before anything
# else runs. CORS alone cannot stop DNS rebinding (see settings.ALLOWED_HOSTS).
if "*" not in settings.ALLOWED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True, "version": app.version})


# Routers
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])
app.include_router(scenes.router, prefix="/api/scenes", tags=["scenes"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(danbooru.router, prefix="/api/danbooru", tags=["danbooru"])
app.include_router(jobs_route.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(trends_route.router, prefix="/api/trends", tags=["trends"])
app.include_router(builder_route.router, prefix="/api/builder", tags=["builder"])
app.include_router(classify_route.router, prefix="/api/classify", tags=["classify"])
app.include_router(presets_route.router, prefix="/api/presets", tags=["presets"])
app.include_router(admin_route.router, prefix="/api/admin", tags=["admin"])
app.include_router(decompose_route.router, prefix="/api/decompose", tags=["decompose"])
app.include_router(llm_route.router, prefix="/api/llm", tags=["llm"])


# Serve the built React frontend if it exists (production-style).
class _SpaStaticFiles(StaticFiles):
    """Fall back to index.html for unknown paths so hard refreshes on
    client-side routes (/trends, /settings, ...) don't 404.

    Starlette raises HTTPException(404) for missing files (it does not
    return a 404 response), so the fallback must catch, not inspect."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not scope["path"].startswith("/api/"):
                response = await super().get_response("index.html", scope)
                response.headers["Cache-Control"] = "no-cache"
                return response
            raise
        # Vite content-hashes /assets/* so those are immutable; everything
        # else (index.html, vendored anime25drig files) keeps a stable name
        # and must revalidate, or updates can pair a fresh index.html with
        # a weeks-stale heuristically-cached script.
        if response.status_code == 200:
            if path.startswith("assets/"):
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                )
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


_frontend_dist = settings.PROJECT_ROOT / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount(
        "/", _SpaStaticFiles(directory=str(_frontend_dist), html=True), name="frontend"
    )
