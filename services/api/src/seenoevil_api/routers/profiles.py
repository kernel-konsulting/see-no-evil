"""Profile CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Profile
from ..schemas import ProfileCreate, ProfileOut, ProfileUpdate


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/profiles", tags=["profiles"])

    @r.get("", response_model=list[ProfileOut])
    def list_profiles(session: Session = Depends(get_session_dep)) -> list[Profile]:
        return list(session.scalars(select(Profile).order_by(Profile.id)))

    @r.get("/{profile_id}", response_model=ProfileOut)
    def get_profile(profile_id: int, session: Session = Depends(get_session_dep)) -> Profile:
        obj = session.get(Profile, profile_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        return obj

    @r.post(
        "",
        response_model=ProfileOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_profile(body: ProfileCreate, session: Session = Depends(get_session_dep)) -> Profile:
        obj = Profile(**body.model_dump())
        session.add(obj)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "profile name already exists") from exc
        session.refresh(obj)
        return obj

    @r.patch(
        "/{profile_id}",
        response_model=ProfileOut,
        dependencies=[Depends(require_admin)],
    )
    def update_profile(
        profile_id: int,
        body: ProfileUpdate,
        session: Session = Depends(get_session_dep),
    ) -> Profile:
        obj = session.get(Profile, profile_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(obj, key, value)
        session.commit()
        session.refresh(obj)
        return obj

    @r.delete(
        "/{profile_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        dependencies=[Depends(require_admin)],
    )
    def delete_profile(profile_id: int, session: Session = Depends(get_session_dep)) -> None:
        obj = session.get(Profile, profile_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "profile not found")
        try:
            session.delete(obj)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, "profile is referenced by one or more devices"
            ) from exc

    return r
