from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.db.session import get_db
from app.models import User
from app.shared.tenant import normalize_tenant_id, set_current_tenant_id
from app.utils import decode_token


class TokenData(BaseModel):
    id: str
    tenant_id: str
    role: str = "owner"


async def get_current_user_token(
    connection: HTTPConnection,
    db: AsyncSession = Depends(get_db),
) -> TokenData:
    token = connection.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (No token found)",
        )

    payload = decode_token(token)
    if payload is None or not payload.get("id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = await db.get(User, payload["id"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    tenant_id = normalize_tenant_id(getattr(user, "tenant_id", None) or str(user.id))
    set_current_tenant_id(tenant_id)
    return TokenData(
        id=str(user.id),
        tenant_id=tenant_id,
        role=str(getattr(user, "role", None) or "owner"),
    )
