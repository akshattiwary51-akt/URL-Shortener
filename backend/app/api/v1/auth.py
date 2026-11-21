from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, Ctx, Session, limit_auth
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_auth)])
async def register(body: RegisterRequest, ctx: Ctx, session: Session) -> UserOut:
    user = await ctx.auth.register(session, body.email, body.password)
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(limit_auth)])
async def login(body: LoginRequest, ctx: Ctx, session: Session) -> TokenResponse:
    user, token, ttl = await ctx.auth.login(session, body.email, body.password)
    return TokenResponse(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(limit_auth)])
async def change_password(body: ChangePasswordRequest, user: CurrentUser, ctx: Ctx, session: Session) -> Response:
    await ctx.auth.change_password(session, user, body.current_password, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(user: CurrentUser, ctx: Ctx, session: Session) -> Response:
    codes = await ctx.auth.delete_account(session, user)
    await ctx.cache.delete_many(codes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
