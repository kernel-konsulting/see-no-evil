"""Device CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Device, Profile
from ..schemas import DeviceCreate, DeviceOut, DeviceUpdate


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/devices", tags=["devices"])

    @r.get("", response_model=list[DeviceOut])
    def list_devices(session: Session = Depends(get_session_dep)) -> list[Device]:
        return list(session.scalars(select(Device).order_by(Device.id)))

    @r.get("/{device_id}", response_model=DeviceOut)
    def get_device(device_id: int, session: Session = Depends(get_session_dep)) -> Device:
        obj = session.get(Device, device_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
        return obj

    @r.post(
        "",
        response_model=DeviceOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_device(body: DeviceCreate, session: Session = Depends(get_session_dep)) -> Device:
        if session.get(Profile, body.profile_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "profile_id does not exist")
        obj = Device(**body.model_dump())
        session.add(obj)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "device with this MAC exists") from exc
        session.refresh(obj)
        return obj

    @r.patch(
        "/{device_id}",
        response_model=DeviceOut,
        dependencies=[Depends(require_admin)],
    )
    def update_device(
        device_id: int,
        body: DeviceUpdate,
        session: Session = Depends(get_session_dep),
    ) -> Device:
        obj = session.get(Device, device_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
        data = body.model_dump(exclude_unset=True)
        if "profile_id" in data and session.get(Profile, data["profile_id"]) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "profile_id does not exist")
        for key, value in data.items():
            setattr(obj, key, value)
        session.commit()
        session.refresh(obj)
        return obj

    @r.delete(
        "/{device_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        dependencies=[Depends(require_admin)],
    )
    def delete_device(device_id: int, session: Session = Depends(get_session_dep)) -> None:
        obj = session.get(Device, device_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
        session.delete(obj)
        session.commit()

    return r
