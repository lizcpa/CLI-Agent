import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from fastapi import Request, HTTPException
from utils.common_sdk.auth import decode_service_jwt
from .config import JWT_SECRET


async def verify_admin_request(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        claims = decode_service_jwt(auth[7:], JWT_SECRET)
        request.state.service_name = claims.get("sub", "unknown")
        request.state.tenant_id = request.headers.get("X-Tenant-ID", "default")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid service token")
