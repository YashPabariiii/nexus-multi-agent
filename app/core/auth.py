import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config.settings import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def generate_api_key() -> str:
    return f"nx_{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    return pwd_context.hash(api_key)


def verify_api_key(api_key: str, api_key_hash: str) -> bool:
    return pwd_context.verify(api_key, api_key_hash)


def create_access_token(tenant_id: uuid.UUID, plan: str) -> tuple[str, int]:
    expires_delta = timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    expire = datetime.now(UTC) + expires_delta
    payload = {"tenant_id": str(tenant_id), "plan": plan, "exp": expire}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


class CurrentTenant(BaseModel):
    tenant_id: uuid.UUID
    plan: str


def decode_token(token: str) -> CurrentTenant:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return CurrentTenant(tenant_id=uuid.UUID(payload["tenant_id"]), plan=payload["plan"])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentTenant:
    return decode_token(credentials.credentials)


def check_brief_limit(tenant, settings_override=None) -> None:
    s = settings_override or settings
    if tenant.plan == "free" and tenant.brief_count_this_month >= s.FREE_TIER_BRIEF_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Free tier brief limit reached")


DEPTH_ORDER = ["quick", "standard", "deep"]


def check_depth_limit(tenant, depth: str, settings_override=None) -> None:
    s = settings_override or settings
    max_free_depth = s.FREE_TIER_DEPTH_LIMIT
    if tenant.plan == "free" and DEPTH_ORDER.index(depth) > DEPTH_ORDER.index(max_free_depth):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{depth.title()} research requires a paid plan")
