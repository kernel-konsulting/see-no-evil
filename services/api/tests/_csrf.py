"""Shared CSRF helper for tests — mirrors UI axios interceptor."""

from __future__ import annotations


def inject_csrf(headers: dict, jar) -> None:
    """Copy seenoevil_csrf cookie to x-csrf-token header if present."""
    try:
        csrf = None
        for cookie in jar:
            if cookie.name == "seenoevil_csrf":
                csrf = cookie.value
                break
        if csrf and "x-csrf-token" not in {k.lower() for k in headers}:
            headers["x-csrf-token"] = csrf
    except Exception:
        pass
