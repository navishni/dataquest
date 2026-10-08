"""Response envelope helpers: {ok,data,request_id} / {ok:false,error,request_id}."""
import uuid
from typing import Any, Callable

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from .errors import AgentError, ErrorCode, HTTP_STATUS


def new_request_id() -> str:
    return uuid.uuid4().hex


def _dump(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if isinstance(data, list):
        return [_dump(d) for d in data]
    if isinstance(data, dict):
        return {k: _dump(v) for k, v in data.items()}
    return data


def ok(data: Any, request_id: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": _dump(data), "request_id": request_id}, status_code=status)


def fail(code: ErrorCode, message: str, request_id: str, details: Any = None) -> JSONResponse:
    err = {"code": code.value, "message": message}
    if details is not None:
        err["details"] = details
    return JSONResponse({"ok": False, "error": err, "request_id": request_id},
                        status_code=HTTP_STATUS.get(code, 500))


def handle(request_id: str, fn: Callable[[], Any], status: int = 200) -> JSONResponse:
    """Run fn and wrap result/exception in the envelope. Never leaks tracebacks or paths."""
    try:
        return ok(fn(), request_id, status)
    except AgentError as e:
        return fail(e.code, e.message, request_id, e.details)
    except ValidationError as e:
        return fail(ErrorCode.INVALID_INPUT, "Invalid input", request_id,
                    [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()])
    except Exception:  # noqa: BLE001 - last resort, no internals exposed
        return fail(ErrorCode.ENGINE_FAILED, "Internal processing error", request_id)
