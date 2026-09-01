"""OPA sidecar adapter for policy decisions.

Builds the OPA input document from the same Python policy inputs and
POSTs it to the OPA sidecar's ``/v1/data/seenoevil/policy/decision`` endpoint.
Python remains canonical — this module is only used when
``policy.engine`` is ``opa`` or ``auto``.
"""

from __future__ import annotations

import logging
from datetime import time
from typing import Any
from urllib.parse import unquote_plus, urlparse

import httpx

from .config import AppConfig
from .policy import DecisionInput, DecisionOutput, GlobalRules, ProfileView

log = logging.getLogger("seenoevil_api.policy_opa")


def _host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    return (parsed.hostname or "").lower()


def _path_query_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    pq = parsed.path or ""
    if parsed.query:
        pq = f"{pq}?{parsed.query}"
    return pq


def _url_text(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    pieces = [url, parsed.netloc, parsed.path, parsed.query]
    return unquote_plus(" ".join(pieces)).lower()


def _extract_youtube_channel(url: str) -> str | None:
    path = _path_query_of(url).split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    first = parts[0]
    if first.startswith("@"):
        return first.lower()
    if len(parts) >= 2 and first in ("channel", "c", "user"):
        return parts[1].lower()
    return None


def _is_youtube_host(host: str) -> bool:
    h = host.lower()
    return h == "youtube.com" or h.endswith(".youtube.com") or h == "youtu.be"


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def build_opa_input(
    profile: ProfileView,
    inputs: DecisionInput,
    *,
    config: AppConfig | None = None,
    global_rules: GlobalRules | None = None,
) -> dict[str, Any]:
    """Build the OPA ``input`` document from Python policy inputs."""
    global_rules = global_rules or GlobalRules()
    host = _host_of(inputs.url)
    path_query = _path_query_of(inputs.url)
    url_text = _url_text(inputs.url)
    youtube_channel: str | None = None
    if _is_youtube_host(host):
        youtube_channel = _extract_youtube_channel(inputs.url)

    # Config thresholds for fallback (Python canonical: profile > runtime > global)
    config_thresholds: dict[str, float] = {}
    if config is not None:
        try:
            raw = config.classifiers.image.thresholds.model_dump()  # type: ignore[attr-defined]
            # Filter to known labels, keep as float
            for k, v in raw.items():
                if isinstance(v, int | float):
                    config_thresholds[k] = float(v)
        except Exception:
            config_thresholds = {}

    # Normalise classifier_scores keys to lower for label matching (OPA lowercases label)
    # Keep original keys but also ensure lower-case lookup works — OPA does lower(parts[-1])
    norm_scores: dict[str, float] = {}
    for k, v in (inputs.classifier_scores or {}).items():
        try:
            norm_scores[str(k)] = float(v)
        except Exception:
            continue

    profile_doc: dict[str, Any] = {
        "image_thresholds": dict(profile.image_thresholds or {}),
        "schedule": dict(profile.schedule or {}),
        "quota_minutes_per_day": int(profile.quota_minutes_per_day or 0),
        "allow_domains": list(profile.allow_domains or []),
        "deny_domains": list(profile.deny_domains or []),
        "deny_url_keywords": list(profile.deny_url_keywords or []),
        "allow_youtube_channels": list(profile.allow_youtube_channels or []),
        "deny_youtube_channels": list(profile.deny_youtube_channels or []),
        "enforce_allowlist": bool(profile.enforce_allowlist),
    }

    global_doc: dict[str, Any] = {
        "allow_domains": list(global_rules.allow_domains or []),
        "deny_domains": list(global_rules.deny_domains or []),
        "deny_url_keywords": list(global_rules.deny_url_keywords or []),
        "enforce_allowlist": bool(global_rules.enforce_allowlist),
        "apply_domain_rules": bool(global_rules.apply_domain_rules),
        "apply_url_rules": bool(global_rules.apply_url_rules),
        "image_thresholds": dict(global_rules.image_thresholds or {}),
    }

    return {
        "url": inputs.url,
        "host": host,
        "path_query": path_query,
        "url_text": url_text,
        "youtube_channel": youtube_channel,
        "classifier_scores": norm_scores,
        "now_dow": int(inputs.now_dow),
        "now_time_minutes": _time_to_minutes(inputs.now_time),
        "minutes_used_today": int(inputs.minutes_used_today),
        "panic_relax": bool(inputs.panic_relax),
        "profile": profile_doc,
        "global_rules": global_doc,
        "config_thresholds": config_thresholds,
    }


def opa_decide(
    opa_input: dict[str, Any],
    *,
    opa_url: str,
    timeout_ms: int = 1500,
) -> DecisionOutput:
    """POST ``opa_input`` to ``opa_url/v1/data/seenoevil/policy/decision``.

    Raises ``ValueError`` or ``httpx.HTTPError`` on failure so the caller can
    decide to fallback or 503.
    """
    base = opa_url.rstrip("/")
    url = f"{base}/v1/data/seenoevil/policy/decision"
    timeout = timeout_ms / 1000.0 if timeout_ms else 1.5
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json={"input": opa_input})
    except httpx.HTTPError:
        raise
    except Exception as exc:
        raise ValueError(f"opa request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise ValueError(f"opa error {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except Exception as exc:
        raise ValueError(f"opa non-json response: {resp.text[:500]}") from exc

    # OPA bundle returns {"result": {"decision": "allow", "reason": "..."}}
    # Some configs return result directly.
    result = data.get("result") if isinstance(data, dict) else None
    if result is None:
        raise ValueError(f"opa missing result field: {data!r:.500}")

    # result may be {"decision": "...", "reason": "..."} or nested
    if isinstance(result, dict) and "decision" in result:
        decision = str(result.get("decision", ""))
        reason = str(result.get("reason", ""))
    elif isinstance(result, dict):
        # Handle {"result": {"decision": ...}} already unwrapped? fallback
        decision = str(result.get("decision", ""))
        reason = str(result.get("reason", ""))
    else:
        raise ValueError(f"opa unexpected result shape: {result!r}")

    if decision not in ("allow", "block"):
        raise ValueError(f"opa invalid decision: {decision!r}")

    return DecisionOutput(decision=decision, reason=reason)  # type: ignore[arg-type]
