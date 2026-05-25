from __future__ import annotations

import argparse
import contextlib
import dataclasses
import html
import json
import math
import os
import socket
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_AUDIT_TIMEOUT_SECONDS = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


@dataclass(slots=True)
class ImageRunResult:
    name: str
    path: str
    source_url: str
    http_status: int
    response_content_type: str
    proxy_blocked: bool
    audit_decision: str
    audit_reason: str
    classifier_scores: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_dataset_dir() -> Path:
    return _repo_root() / "test_data"


def _default_output_dir() -> Path:
    return _default_dataset_dir() / "proxy-regression"


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _image_files(dataset_dir: Path) -> list[Path]:
    files = [
        path
        for path in sorted(dataset_dir.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if not files:
        msg = f"no images found in {dataset_dir}"
        raise SystemExit(msg)
    return files


def _build_opener(*, proxy_url: str | None = None, cookie_jar=None):
    handlers: list[Any] = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    return urllib.request.build_opener(*handlers)


def _request_json(opener, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = _json_dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        payload = response.read()
    if not payload:
        return None
    return json.loads(payload)


def login(api_url: str, username: str, password: str):
    import http.cookiejar

    cookie_jar = http.cookiejar.CookieJar()
    opener = _build_opener(cookie_jar=cookie_jar)
    _request_json(
        opener,
        "POST",
        urllib.parse.urljoin(api_url.rstrip("/") + "/", "v1/auth/login"),
        {"email": username, "password": password},
    )
    return opener


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def static_image_server(dataset_dir: Path):
    host = "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port = sock.getsockname()[1]
    handler = partial(_QuietHandler, directory=str(dataset_dir))
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fetch_through_proxy(proxy_url: str, image_url: str) -> tuple[int, str, bool]:
    opener = _build_opener(proxy_url=proxy_url)
    request = urllib.request.Request(image_url, method="GET")
    with opener.open(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        _ = response.read()
        headers = response.headers
        return (
            response.status,
            headers.get_content_type(),
            headers.get("X-See-No-Evil-Blocked") == "true",
        )


def fetch_audit_entries(api_opener, api_url: str, *, limit: int = 250) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    before_id: int | None = None
    while True:
        query = {"limit": limit}
        if before_id is not None:
            query["before_id"] = before_id
        url = urllib.parse.urljoin(api_url.rstrip("/") + "/", "v1/audit")
        page = _request_json(api_opener, "GET", f"{url}?{urllib.parse.urlencode(query)}")
        if not page:
            break
        assert isinstance(page, list)
        entries.extend(page)
        if len(page) < limit:
            break
        before_id = int(page[-1]["id"])
    return entries


def wait_for_run_audit_entries(
    api_opener,
    api_url: str,
    requested_urls: set[str],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    matches: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        matches = {
            entry["url"]: entry
            for entry in fetch_audit_entries(api_opener, api_url)
            if entry.get("url") in requested_urls
        }
        if requested_urls.issubset(matches):
            return matches
        time.sleep(poll_interval_seconds)
    missing = sorted(requested_urls.difference(matches))
    msg = "timed out waiting for audit rows for: " + ", ".join(missing)
    raise TimeoutError(msg)


def normalize_classifier_scores(raw_scores: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for label, value in raw_scores.items():
        if isinstance(value, int | float) and math.isfinite(value):
            normalized[str(label)] = float(value)
    return normalized


def score_delta_map(
    baseline_scores: dict[str, float],
    current_scores: dict[str, float],
    *,
    tolerance: float,
) -> list[dict[str, float | str]]:
    deltas: list[dict[str, float | str]] = []
    for label in sorted(set(baseline_scores) | set(current_scores)):
        baseline = baseline_scores.get(label, 0.0)
        current = current_scores.get(label, 0.0)
        delta = current - baseline
        if abs(delta) <= tolerance:
            continue
        deltas.append(
            {
                "label": label,
                "baseline": round(baseline, 6),
                "current": round(current, 6),
                "delta": round(delta, 6),
            }
        )
    deltas.sort(key=lambda item: abs(float(item["delta"])), reverse=True)
    return deltas


def compare_runs(
    baseline_report: dict[str, Any],
    current_report: dict[str, Any],
    *,
    tolerance: float,
) -> dict[str, Any]:
    baseline_images = {image["name"]: image for image in baseline_report.get("images", [])}
    current_images = {image["name"]: image for image in current_report.get("images", [])}
    new_images = sorted(set(current_images) - set(baseline_images))

    comparison_images: list[dict[str, Any]] = []
    decision_changes = 0
    score_changes = 0

    for name in sorted(current_images):
        current = dict(current_images[name])
        baseline = baseline_images.get(name)
        if baseline is None:
            current["comparison"] = {"status": "new"}
            comparison_images.append(current)
            continue

        deltas = score_delta_map(
            normalize_classifier_scores(baseline.get("classifier_scores", {})),
            normalize_classifier_scores(current.get("classifier_scores", {})),
            tolerance=tolerance,
        )
        decision_changed = (
            baseline.get("audit_decision") != current.get("audit_decision")
            or baseline.get("proxy_blocked") != current.get("proxy_blocked")
            or baseline.get("audit_reason") != current.get("audit_reason")
        )
        if decision_changed:
            decision_changes += 1
        if deltas:
            score_changes += 1
        current["comparison"] = {
            "status": "changed" if (decision_changed or deltas) else "same",
            "baseline_decision": baseline.get("audit_decision"),
            "baseline_reason": baseline.get("audit_reason"),
            "baseline_proxy_blocked": baseline.get("proxy_blocked"),
            "decision_changed": decision_changed,
            "score_deltas": deltas,
        }
        comparison_images.append(current)

    removed_images = sorted(set(baseline_images) - set(current_images))

    return {
        "summary": {
            "baseline_images": len(baseline_images),
            "current_images": len(current_images),
            "new_images": len(new_images),
            "removed_images": len(removed_images),
            "decision_changes": decision_changes,
            "score_changes": score_changes,
            "tolerance": tolerance,
        },
        "removed_images": removed_images,
        "images": comparison_images,
    }


def has_meaningful_changes(comparison: dict[str, Any]) -> bool:
    summary = comparison.get("summary", {})
    return any(
        int(summary.get(key, 0)) > 0
        for key in ("new_images", "removed_images", "decision_changes", "score_changes")
    )


def _score_rows(scores: dict[str, float]) -> str:
    if not scores:
        return '<div class="empty">no scores</div>'

    parts: list[str] = []
    for label, value in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        pct = max(0.0, min(100.0, value * 100.0))
        parts.append(
            f'<div class="score"><span class="label">{html.escape(label)}</span>'
            f'<div class="bar"><span style="width:{pct:.2f}%"></span></div>'
            f'<span class="value">{value:.4f}</span></div>'
        )
    return "".join(parts)


def _preview_html(image: dict[str, Any], *, report_dir: Path) -> str:
    if image.get("proxy_blocked") or image.get("audit_decision") == "block":
        return (
            '<div class="preview hidden">'
            '<div class="preview-label">preview hidden</div>'
            "<p>blocked by proxy</p>"
            "</div>"
        )

    rel_path = os.path.relpath(Path(image["path"]), report_dir)
    return (
        f'<img src="{html.escape(rel_path)}" alt="{html.escape(image["name"])}" loading="lazy" />'
    )


def render_html_report(report: dict[str, Any], *, report_dir: Path) -> str:
    comparison = report.get("comparison") or {
        "summary": {},
        "images": report.get("images", []),
    }
    cards: list[str] = []

    for image in comparison.get("images", []):
        scores = normalize_classifier_scores(image.get("classifier_scores", {}))
        comp = image.get("comparison", {})
        deltas = comp.get("score_deltas", [])
        delta_html = "".join(
            (
                f"<li><strong>{html.escape(str(delta['label']))}</strong>: "
                f"{float(delta['baseline']):.4f} → {float(delta['current']):.4f} "
                f"({float(delta['delta']):+.4f})</li>"
            )
            for delta in deltas
        )
        if not delta_html:
            delta_html = "<li>no score drift above tolerance</li>"

        cards.append(
            """
            <article class="card">
                            {preview}
              <div class="card-body">
                <div class="row">
                  <h2>{name}</h2>
                  <span class="pill {decision_class}">{decision}</span>
                </div>
                <p class="meta">reason: {reason}</p>
                <p class="meta">proxy blocked: <strong>{blocked}</strong></p>
                <p class="meta">comparison: <strong>{status}</strong></p>
                <div class="scores">{scores}</div>
                <ul class="deltas">{deltas}</ul>
              </div>
            </article>
            """.format(
                preview=_preview_html(image, report_dir=report_dir),
                name=html.escape(image["name"]),
                decision=html.escape(str(image.get("audit_decision", "unknown"))),
                decision_class="blocked" if image.get("proxy_blocked") else "allowed",
                reason=html.escape(str(image.get("audit_reason", ""))),
                blocked="yes" if image.get("proxy_blocked") else "no",
                status=html.escape(str(comp.get("status", "same"))),
                scores=_score_rows(scores),
                deltas=delta_html,
            )
        )

    summary = comparison.get("summary", {})
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>see-no-evil proxy image regression</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f4ec;
      --panel: rgba(255,255,255,0.86);
      --ink: #1f2230;
      --muted: #5d6578;
      --line: rgba(31,34,48,0.12);
      --accent: #bf5a36;
      --accent-soft: #f0c8b6;
      --ok: #276749;
      --warn: #8b2f2f;
      --shadow: 0 16px 40px rgba(26, 21, 16, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      background:
        radial-gradient(circle at top left, rgba(191,90,54,0.20), transparent 28%),
        linear-gradient(180deg, #fffaf3 0%, var(--bg) 55%, #efe6d8 100%);
      color: var(--ink);
    }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 32px 20px 64px; }}
    .hero {{ display: grid; gap: 12px; margin-bottom: 28px; }}
    .hero h1 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 4rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    .hero p {{ margin: 0; max-width: 70ch; color: var(--muted); font-size: 1rem; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 28px;
    }}
    .stat, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .stat {{ border-radius: 18px; padding: 16px; }}
    .stat strong {{ display: block; font-size: 1.7rem; margin-top: 6px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
      gap: 16px;
    }}
    .card {{ border-radius: 24px; overflow: hidden; }}
    .card img {{
      display: block;
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      background: #e9dcc7;
    }}
        .preview {{
            width: 100%;
            aspect-ratio: 4 / 3;
        }}
        .preview.hidden {{
            display: grid;
            place-items: center;
            gap: 8px;
            padding: 24px;
            background: linear-gradient(135deg, #f1e3dc, #ead8cf);
            color: var(--warn);
            text-align: center;
        }}
        .preview.hidden p {{
            margin: 0;
            font-size: 0.95rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .preview-label {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            padding: 6px 12px;
            background: rgba(139,47,47,0.10);
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}
    .card-body {{ padding: 16px; display: grid; gap: 10px; }}
    .row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }}
    .row h2 {{
      margin: 0;
      font-size: 1.15rem;
      line-height: 1.1;
      word-break: break-word;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .pill.allowed {{ color: var(--ok); background: rgba(39,103,73,0.10); }}
    .pill.blocked {{ color: var(--warn); background: rgba(139,47,47,0.10); }}
    .meta {{ margin: 0; color: var(--muted); font-size: 0.92rem; }}
    .scores {{ display: grid; gap: 8px; }}
    .score {{
      display: grid;
      grid-template-columns: minmax(90px, 130px) 1fr 62px;
      gap: 10px;
      align-items: center;
      font-size: 0.9rem;
    }}
    .label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar {{
      height: 9px;
      border-radius: 999px;
      background: rgba(31,34,48,0.08);
      overflow: hidden;
    }}
    .bar span {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent-soft), var(--accent));
    }}
    .value {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .deltas {{ margin: 0; padding-left: 18px; color: var(--muted); font-size: 0.9rem; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    @media (max-width: 640px) {{
      main {{ padding-inline: 14px; }}
      .score {{ grid-template-columns: 1fr; }}
      .value {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <p>Proxy-driven regression snapshot</p>
      <h1>Image decisions and score drift</h1>
      <p>
        Run {generated_at} against {proxy_url} and {api_url}. This report shows how the
        proxy classified the local test dataset and whether scores drifted relative to the
        selected baseline.
      </p>
    </section>
    <section class="stats">
      <div class="stat"><span>Images</span><strong>{images}</strong></div>
      <div class="stat"><span>Decision changes</span><strong>{decision_changes}</strong></div>
      <div class="stat"><span>Score changes</span><strong>{score_changes}</strong></div>
      <div class="stat"><span>Removed from baseline</span><strong>{removed_images}</strong></div>
    </section>
    <section class="cards">{cards}</section>
  </main>
</body>
</html>
""".format(
        generated_at=html.escape(str(report.get("generated_at", ""))),
        proxy_url=html.escape(str(report.get("proxy_url", ""))),
        api_url=html.escape(str(report.get("api_url", ""))),
        images=len(report.get("images", [])),
        decision_changes=int(summary.get("decision_changes", 0)),
        score_changes=int(summary.get("score_changes", 0)),
        removed_images=int(summary.get("removed_images", 0)),
        cards="".join(cards),
    )


def generate_report(
    *,
    proxy_url: str,
    api_url: str,
    api_opener,
    dataset_dir: Path,
    audit_timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    run_id = f"proxy-regression-{int(time.time())}"
    files = _image_files(dataset_dir)
    requested_urls: set[str] = set()
    requested_by_name: dict[str, str] = {}
    http_results: dict[str, tuple[int, str, bool]] = {}

    with static_image_server(dataset_dir) as source_base_url:
        for image in files:
            source_url = (
                f"{source_base_url}/{urllib.parse.quote(image.name)}"
                f"?run_id={urllib.parse.quote(run_id)}"
            )
            requested_urls.add(source_url)
            requested_by_name[image.name] = source_url
            http_results[image.name] = fetch_through_proxy(proxy_url, source_url)

        audit_rows = wait_for_run_audit_entries(
            api_opener,
            api_url,
            requested_urls,
            timeout_seconds=audit_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    results: list[ImageRunResult] = []
    for image in files:
        source_url = requested_by_name[image.name]
        status, content_type, proxy_blocked = http_results[image.name]
        audit = audit_rows[source_url]
        results.append(
            ImageRunResult(
                name=image.name,
                path=str(image),
                source_url=source_url,
                http_status=status,
                response_content_type=content_type,
                proxy_blocked=proxy_blocked,
                audit_decision=str(audit.get("decision", "")),
                audit_reason=str(audit.get("reason", "")),
                classifier_scores=normalize_classifier_scores(audit.get("classifier_scores", {})),
            )
        )

    blocked = sum(1 for result in results if result.proxy_blocked)
    return {
        "generated_at": _iso_now(),
        "proxy_url": proxy_url,
        "api_url": api_url,
        "dataset_dir": str(dataset_dir),
        "images": [result.to_dict() for result in results],
        "summary": {
            "total_images": len(results),
            "proxy_blocked": blocked,
            "audit_blocked": sum(1 for result in results if result.audit_decision == "block"),
            "audit_allowed": sum(1 for result in results if result.audit_decision == "allow"),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drive local test images through the proxy, then compare audit scores "
            "to a saved baseline."
        )
    )
    parser.add_argument(
        "--proxy-url",
        default=os.environ.get("SEENOEVIL_PROXY_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("SEENOEVIL_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--username", default=os.environ.get("SEENOEVIL_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("SEENOEVIL_PASSWORD"))
    parser.add_argument("--dataset-dir", type=Path, default=_default_dataset_dir())
    parser.add_argument("--output-dir", type=Path, default=_default_output_dir())
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--fail-on-change", action="store_true")
    parser.add_argument("--score-tolerance", type=float, default=0.02)
    parser.add_argument(
        "--audit-timeout",
        type=float,
        default=DEFAULT_AUDIT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.username or not args.password:
        raise SystemExit(
            "username/password required; pass --username/--password or set "
            "SEENOEVIL_EMAIL/SEENOEVIL_PASSWORD"
        )

    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.output_dir.resolve()
    _ensure_dir(output_dir)

    report_path = output_dir / "latest.json"
    html_path = output_dir / "latest.html"
    baseline_path = args.baseline.resolve() if args.baseline else output_dir / "baseline.json"

    api_opener = login(args.api_url, args.username, args.password)
    report = generate_report(
        proxy_url=args.proxy_url,
        api_url=args.api_url,
        api_opener=api_opener,
        dataset_dir=dataset_dir,
        audit_timeout_seconds=args.audit_timeout,
        poll_interval_seconds=args.poll_interval,
    )

    if baseline_path.exists():
        report["comparison"] = compare_runs(
            _load_json(baseline_path),
            report,
            tolerance=args.score_tolerance,
        )
    else:
        report["comparison"] = {
            "summary": {
                "baseline_images": 0,
                "current_images": len(report.get("images", [])),
                "new_images": len(report.get("images", [])),
                "removed_images": 0,
                "decision_changes": 0,
                "score_changes": 0,
                "tolerance": args.score_tolerance,
            },
            "removed_images": [],
            "images": [
                {**image, "comparison": {"status": "new"}} for image in report.get("images", [])
            ],
        }

    report_path.write_text(_json_dumps(report) + "\n", encoding="utf-8")
    html_path.write_text(
        render_html_report(report, report_dir=html_path.parent),
        encoding="utf-8",
    )

    if args.write_baseline:
        baseline_payload = {key: value for key, value in report.items() if key != "comparison"}
        baseline_path.write_text(_json_dumps(baseline_payload) + "\n", encoding="utf-8")

    print(f"wrote report: {report_path}")
    print(f"wrote html: {html_path}")
    if args.write_baseline:
        print(f"updated baseline: {baseline_path}")

    if args.fail_on_change and has_meaningful_changes(report["comparison"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
