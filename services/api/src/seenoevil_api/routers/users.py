"""Multi-user admin management."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import auth as auth_mod


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: str
    disabled: bool
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: str = "admin"


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8)
    role: str | None = None
    disabled: bool | None = None


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/users", tags=["users"])

    @r.get("", response_model=list[UserOut])
    def list_users(
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ) -> list:
        return auth_mod.list_users(session)

    @r.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
    def create_user(
        body: UserCreate,
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ):
        try:
            return auth_mod.create_user(session, body.email, body.password, body.role)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @r.patch("/{user_id}", response_model=UserOut)
    def update_user(
        user_id: int,
        body: UserUpdate,
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ):
        try:
            return auth_mod.update_user(
                session,
                user_id,
                password=body.password,
                role=body.role,
                disabled=body.disabled,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @r.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    def delete_user(
        user_id: int,
        session: Session = Depends(get_session_dep),
        current: str = Depends(require_admin),
    ):
        # Prevent locking yourself out: refuse if it would leave 0 users.
        users = auth_mod.list_users(session)
        if len(users) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete the last admin user")
        target = next((u for u in users if u.id == user_id), None)
        if target is not None and target.email == current:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete your own account")
        auth_mod.delete_user(session, user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return r
