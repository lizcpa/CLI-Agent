from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class APIResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None


def success_response(data: Any = None) -> APIResponse:
    return APIResponse(code=0, message="success", data=data)


def error_response(code: int, message: str, data: Any = None) -> APIResponse:
    return APIResponse(code=code, message=message, data=data)


def async_task_response(task_id: str, estimated_seconds: int = 0) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data={"task_id": task_id, "status": "queued", "estimated_seconds": estimated_seconds},
    )


def paginated_response(
    items: list[Any], total: int, page: int, page_size: int
) -> APIResponse:
    return APIResponse(
        code=0,
        message="success",
        data={
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
        },
    )
