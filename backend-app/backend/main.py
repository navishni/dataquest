"""ParseFusion backend entrypoint:  uvicorn backend.main:app  (terminate TLS in front; HSTS is set here)."""
import os

def _load_dotenv() -> None:
    """Read KEY=VALUE lines from .env in the working directory. Empty values and already-set variables are skipped."""
    try:
        with open(".env", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .common.envelope import fail, new_request_id
from .common.errors import ErrorCode
from .common import notify
from .routers import agents, platform

@asynccontextmanager
async def lifespan(_: FastAPI):
    platform.bootstrap_admin()
    if notify.notifier.enabled():
        notify.notifier.start_worker()
    yield


app = FastAPI(title="ParseFusion Backend", version="1.0.0", lifespan=lifespan)
# Browser access for the frontend dev server. Override with PF_CORS_ORIGINS (comma separated).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("PF_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.include_router(platform.router)
app.include_router(agents.router)


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    return fail(ErrorCode.INVALID_INPUT, "Invalid input", new_request_id(),
                [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()])


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return fail(ErrorCode.ENGINE_FAILED, "Internal processing error", new_request_id())


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp
