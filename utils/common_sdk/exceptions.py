from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, code: int, message: str, http_status: int = 500, data: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data or {}
        super().__init__(message)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(code=404, message=message, http_status=404)


class ValidationException(AppException):
    def __init__(self, message: str = "Validation failed", data: dict | None = None) -> None:
        super().__init__(code=422, message=message, http_status=422, data=data)


class AuthException(AppException):
    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(code=401, message=message, http_status=401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(code=403, message=message, http_status=403)


class ServiceException(AppException):
    def __init__(self, message: str = "Internal service error", data: dict | None = None) -> None:
        super().__init__(code=500, message=message, http_status=500, data=data)


class ContentFilteredException(AppException):
    def __init__(self, message: str = "Content filtered by safety check", data: dict | None = None) -> None:
        super().__init__(code=451, message=message, http_status=451, data=data)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message, "data": exc.data},
    )
