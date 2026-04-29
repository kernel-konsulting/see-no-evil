"""Admin auth endpoints (login, logout, first-time setup, OIDC)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import oidc
from ..auth import (
    admin_is_configured,
    clear_session,
    issue_session,
    set_admin_password,
    verify_admin,
)
from ..config import AppConfig
from ..schemas import LoginRequest, LoginResponse, OIDCStartResponse, SetupRequest


class MeResponse(BaseModel):
    email: str
    role: str


class OIDCInfo(BaseModel):
    enabled: bool
    label: str = "Sign in with SSO"


def make_router(get_session_dep, get_config, current_user) -> APIRouter:
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

    @r.get("/me", response_model=MeResponse)
    def me(current: tuple[str, str] = Depends(current_user)) -> MeResponse:
        email, role = current
        return MeResponse(email=email, role=role)

    @r.get("/oidc/info", response_model=OIDCInfo)
    def oidc_info(config: AppConfig = Depends(get_config)) -> OIDCInfo:
        cfg = config.auth.oidc
        return OIDCInfo(enabled=bool(cfg.enabled and cfg.redirect_url))

    @r.get("/oidc/start", response_model=OIDCStartResponse)
    def oidc_start(
        session: Session = Depends(get_session_dep),
        config: AppConfig = Depends(get_config),
    ) -> OIDCStartResponse:
        cfg = config.auth.oidc
        if not cfg.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "oidc disabled")
        if not cfg.redirect_url:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "auth.oidc.redirect_url not configured"
            )
        try:
            started = oidc.start_flow(cfg, session, redirect_url=cfg.redirect_url)
        except ValueError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
        session.commit()
        return OIDCStartResponse(authorize_url=started.authorize_url, state=started.state)

    @r.get("/oidc/callback")
    def oidc_callback(
        response: Response,
        code: str = Query(...),
        state: str = Query(...),
        session: Session = Depends(get_session_dep),
        config: AppConfig = Depends(get_config),
    ) -> RedirectResponse:
        cfg = config.auth.oidc
        if not cfg.enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "oidc disabled")
        try:
            finished = oidc.finish_flow(cfg, session, code=code, state=state)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        issue_session(session, redirect, finished.email)
        session.commit()
        return redirect

    return r
