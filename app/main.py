from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.database import create_schemas, engine
from app.core.logging import setup_logging
from app.core.rate_limiter import limiter
from app.middleware.request_logger import RequestLoggingMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.jobs.router import router as jobs_router

setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_schemas()
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, _exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})


app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(jobs_router, prefix="/jobs", tags=["Jobs"])

app.add_middleware(RequestLoggingMiddleware)

origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if settings.DEBUG else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health() -> dict[str, str]:
    return {"status": "TaskForge running"}
