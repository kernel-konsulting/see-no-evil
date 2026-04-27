"""Admin auth endpoints (login, logout, first-time setup)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..auth import (
    admin_is_configured,
    clear_session,
    issue_session,
    set_admin_password,
    verify_admin,
)
from ..schemas import LoginRequest, LoginResponse, SetupRequest


def make_router(get_session_dep) -> APIRouter:
    r = APIRouter(prefix="/v1/auth", tags=["auth"])

    @r.post("/setup", response_model=LoginResponse)
    def setup(body: SetupRequest, session: Session = Depends(get_session_dep)) -> LoginResponse:
        # Setup is only callable while no admin exists. After that, password
        # changes go through an authenticated endpoint (added in M1.5).
        if admin_is_configured(session):
            raise HTTPException(status.HTTP_409_CONFLICT, "admin already configured")
        try:
            set_admin_password(session, body.email, body.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        session.commit()
        return LoginResponse(email=body.email)

    @r.post("/login", response_model=LoginResponse)
    def login(
        body: LoginRequest,
        response: Response,
        session: Session = Depends(get_session_dep),
    ) -> LoginResponse:
        if not verify_admin(session, body.email, body.password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        issue_session(session, response, body.email)
        session.commit()
        return LoginResponse(email=body.email)

    @r.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
    def logout(response: Response) -> None:
        clear_session(response)

    return r
