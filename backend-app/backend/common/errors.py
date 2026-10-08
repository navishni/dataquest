"""Stable error codes and the exception every agent raises."""
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    EMPTY_FILE = "EMPTY_FILE"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
    CORRUPT_FILE = "CORRUPT_FILE"
    TOO_LARGE = "TOO_LARGE"
    FORBIDDEN = "FORBIDDEN"
    ENGINE_FAILED = "ENGINE_FAILED"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"


HTTP_STATUS = {
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.FORMAT_MISMATCH: 415,
    ErrorCode.EMPTY_FILE: 400,
    ErrorCode.PASSWORD_REQUIRED: 422,
    ErrorCode.CORRUPT_FILE: 422,
    ErrorCode.TOO_LARGE: 413,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.ENGINE_FAILED: 500,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.CONFLICT: 409,
}


class AgentError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
