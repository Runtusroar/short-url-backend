"""Stable public API errors and exception handlers."""

import logging
from enum import StrEnum

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    CONFLICT = "CONFLICT"
    DOMAIN_CONFLICT = "DOMAIN_CONFLICT"
    DOMAIN_IN_USE = "DOMAIN_IN_USE"
    IP_BLACKLIST_CONFLICT = "IP_BLACKLIST_CONFLICT"
    INVALID_CURSOR = "INVALID_CURSOR"
    LAST_ADMIN_REQUIRED = "LAST_ADMIN_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SHORT_CODE_CONFLICT = "SHORT_CODE_CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    USERNAME_CONFLICT = "USERNAME_CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class APIError(Exception):
    """Base class for API failures whose public payload is safe to expose."""

    def __init__(self, code: str | ErrorCode, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(APIError):
    def __init__(self, resource: str = "资源"):
        super().__init__(ErrorCode.NOT_FOUND, f"{resource}不存在", status.HTTP_404_NOT_FOUND)


class ConflictError(APIError):
    def __init__(self, message: str = "资源冲突", code: str | ErrorCode = ErrorCode.CONFLICT):
        super().__init__(code, message, status.HTTP_409_CONFLICT)


class PermissionDeniedError(APIError):
    def __init__(self, message: str = "无权访问"):
        super().__init__(ErrorCode.PERMISSION_DENIED, message, status.HTTP_403_FORBIDDEN)


class UnauthorizedError(APIError):
    def __init__(self, message: str = "未授权"):
        super().__init__(ErrorCode.UNAUTHORIZED, message, status.HTTP_401_UNAUTHORIZED)


def _format_validation_errors(errors: list[dict]) -> str:
    if not errors:
        return "请求参数错误"
    first = errors[0]
    loc = first.get("loc", [])
    field = loc[-1] if loc else "参数"
    return f"{field}{first.get('msg', '格式不正确')}"


def integrity_constraint_name(exc: IntegrityError) -> str | None:
    """Read a PostgreSQL constraint diagnostic without parsing driver error text.

    psycopg exposes the name on ``orig.diag`` while asyncpg exposes it directly
    on the exception.  Both are structured diagnostics supplied by PostgreSQL.
    """
    orig = getattr(exc, "orig", None)
    diagnostic = getattr(orig, "diag", None)
    for source in (diagnostic, orig):
        constraint_name = getattr(source, "constraint_name", None)
        if isinstance(constraint_name, str) and constraint_name:
            return constraint_name
    return None


def _integrity_error_message(exc: IntegrityError) -> str:
    orig = getattr(exc, "orig", None)
    constraint_messages = {
        "uq_domain_short_code": "该短码在当前域名下已存在",
        "users_username_key": "用户名已存在",
        "ip_blacklist_ip_key": "该 IP 已在黑名单中",
        "uq_user_domain": "用户已拥有该域名权限",
        "uq_short_link_user": "该用户已被授权查看此短链",
    }
    if (constraint_name := integrity_constraint_name(exc)) in constraint_messages:
        return constraint_messages[constraint_name]
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate == "23505":
        return "数据已存在"
    if sqlstate == "23503":
        return "关联资源不存在"
    return "数据操作失败"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": None},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": f"HTTP_{exc.status_code}", "message": exc.detail, "details": None},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": _format_validation_errors(exc.errors()),
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": _format_validation_errors(exc.errors()),
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": ErrorCode.CONFLICT, "message": _integrity_error_message(exc), "details": None},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API exception for %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None},
        )
