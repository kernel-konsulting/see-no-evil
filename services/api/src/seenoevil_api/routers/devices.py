"""Device CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..models import Device, Profile
from ..schemas import (
    DeviceCreate,
    DeviceOut,
    DeviceUpdate,
    DiscoverRequest,
    DiscoverResponse,
    DiscoverResponseItem,
)


def make_router(get_session_dep, require_admin, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1/devices", tags=["devices"])

    @r.get("", response_model=list[DeviceOut], dependencies=[Depends(require_admin)])
    def list_devices(session: Session = Depends(get_session_dep)) -> list[Device]:
        return list(session.scalars(select(Device).order_by(Device.id)))

    @r.get("/{device_id}", response_model=DeviceOut, dependencies=[Depends(require_admin)])
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

    @r.post(
        "/discover",
        response_model=DiscoverResponse,
        dependencies=[Depends(require_admin)],
    )
    def discover_devices(
        body: DiscoverRequest,
        session: Session = Depends(get_session_dep),
        config: AppConfig = Depends(get_config),
    ) -> DiscoverResponse:
        """Upsert devices observed by the scanner.

        New MACs are created and assigned to the configured default profile.
        Existing devices have ``last_seen_at`` refreshed and (optionally) the
        hostname populated if it was previously empty. Profile assignment is
        never overwritten by the scanner.
        """
        default_profile = session.scalars(
            select(Profile).where(Profile.name == config.devices.default_profile)
        ).first()
        if default_profile is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"default profile {config.devices.default_profile!r} not found",
            )

        items: list[DiscoverResponseItem] = []
        now = datetime.now(UTC).replace(tzinfo=None)
        for d in body.devices:
            existing = session.scalars(select(Device).where(Device.mac == d.mac)).first()
            if existing is not None:
                existing.last_seen_at = now
                if not existing.name and d.hostname:
                    existing.name = d.hostname
                if d.ip:
                    existing.ip = d.ip
                if d.vendor and not existing.vendor:
                    existing.vendor = d.vendor
                items.append(DiscoverResponseItem(mac=d.mac, device_id=existing.id, created=False))
                continue
            new_dev = Device(
                mac=d.mac,
                name=d.hostname,
                profile_id=default_profile.id,
                last_seen_at=now,
                ip=d.ip,
                vendor=d.vendor,
            )
            session.add(new_dev)
            session.flush()
            items.append(DiscoverResponseItem(mac=d.mac, device_id=new_dev.id, created=True))
        session.commit()
        return DiscoverResponse(
            profile_id=default_profile.id,
            profile_name=default_profile.name,
            items=items,
        )

    return r
