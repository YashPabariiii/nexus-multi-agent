from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, generate_api_key, hash_api_key, verify_api_key
from app.core.metrics import limiter
from app.db.session import get_db
from app.models.tenant import Tenant
from app.schemas.auth import RegisterRequest, RegisterResponse, TokenRequest, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def register(request: Request, payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(select(Tenant).where(Tenant.email == payload.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant already registered")

    api_key = generate_api_key()
    tenant = Tenant(name=payload.name, email=payload.email, api_key_hash=hash_api_key(api_key), plan="free")
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    return RegisterResponse(tenant_id=tenant.id, api_key=api_key, plan=tenant.plan)


@router.post("/token", response_model=TokenResponse)
@limiter.limit("20/minute")
async def token(request: Request, payload: TokenRequest, db: AsyncSession = Depends(get_db)):
    # ponytail: O(n) bcrypt scan over tenants; add a lookup index (e.g. key prefix) if tenant count grows large
    result = await db.scalars(select(Tenant))
    for tenant in result:
        if verify_api_key(payload.api_key, tenant.api_key_hash):
            access_token, expires_in = create_access_token(tenant.id, tenant.plan)
            return TokenResponse(access_token=access_token, expires_in=expires_in)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
