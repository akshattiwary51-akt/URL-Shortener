"""Registration, login and account management."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, EmailTaken
from app.core.security import create_access_token, hash_password, verify_dummy, verify_password
from app.database.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def register(self, session: AsyncSession, email: str, password: str) -> User:
        user = User(email=normalize_email(email), hashed_password=await hash_password(self._settings, password))
        session.add(user)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise EmailTaken() from exc
        return user

    async def login(self, session: AsyncSession, email: str, password: str) -> tuple[User, str, int]:
        user = (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()
        if user is None:
            await verify_dummy(self._settings, password)
            raise AuthenticationError("Invalid email or password.", code="invalid_credentials")
        if not await verify_password(self._settings, user.hashed_password, password):
            raise AuthenticationError("Invalid email or password.", code="invalid_credentials")
        token, ttl = create_access_token(self._settings, user.id)
        return user, token, ttl

    async def get_user(self, session: AsyncSession, user_id: int) -> User | None:
        return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    async def change_password(self, session: AsyncSession, user: User, current: str, new: str) -> None:
        if not await verify_password(self._settings, user.hashed_password, current):
            raise AuthenticationError("Current password is incorrect.", code="invalid_credentials")
        user.hashed_password = await hash_password(self._settings, new)
        await session.commit()

    async def delete_account(self, session: AsyncSession, user: User) -> list[str]:
        """Delete the user; their URLs and analytics cascade. Returns the removed short codes."""
        from app.database.models import Url

        codes = list((await session.execute(select(Url.short_code).where(Url.owner_id == user.id))).scalars())
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()
        return codes
