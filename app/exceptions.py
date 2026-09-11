"""Global exception handling and custom API errors."""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ErrorCode


class APIError(Exception):
    """Base class for all API errors that should be returned to the client."""

    def __init__(self, code: str | ErrorCode, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(APIError):
    def __init__(self, resource: str = "资源"):
        super().__init__(
            code=ErrorCode.NOT_FOUND,
            message=f"{resource}不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictError(APIError):
    def __init__(self, message: str = "资源冲突", code: str | ErrorCode = ErrorCode.CONFLICT):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
        )


class PermissionDeniedError(APIError):
    def __init__(self, message: str = "无权访问"):
        super().__init__(
            code=ErrorCode.PERMISSION_DENIED,
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class UnauthorizedError(APIError):
    def __init__(self, message: str = "未授权"):
        super().__init__(
            code=ErrorCode.UNAUTHORIZED,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


def _format_validation_errors(errors: list[dict]) -> str:
    """Extract a readable Chinese message from Pydantic validation errors."""
    if not errors:
        return "请求参数错误"
    first = errors[0]
    loc = first.get("loc", [])
    field = loc[-1] if loc else "参数"
    msg = first.get("msg", "格式不正确")
    return f"{field}{msg}"


def _integrity_error_message(exc: IntegrityError) -> str:
    """Map common PostgreSQL integrity errors to Chinese messages."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return "数据操作失败"
    err_msg = str(orig).lower()
    if "unique constraint" in err_msg:
        if "uq_domain_short_code" in err_msg:
            return "该短码在当前域名下已存在"
        if "username" in err_msg:
            return "用户名已存在"
        if "ip_blacklist_ip_key" in err_msg:
            return "该 IP 已在黑名单中"
        if "uq_user_domain" in err_msg:
            return "用户已拥有该域名权限"
        if "uq_short_link_user" in err_msg:
            return "该用户已被授权查看此短链"
        return "数据已存在"
    if "foreign key" in err_msg:
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
            content={
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail,
                "details": None,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        message = _format_validation_errors(exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": message,
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: ValidationError):
        message = _format_validation_errors(exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": message,
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": ErrorCode.CONFLICT,
                "message": _integrity_error_message(exc),
                "details": None,
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        # In production you would log the traceback here.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": None},
        )
