from __future__ import annotations

from pathlib import Path

from seenoevil_image_classifier import regression


def test_compare_runs_flags_decision_and_score_changes() -> None:
    baseline = {
        "images": [
            {
                "name": "sample.avif",
                "classifier_scores": {"image:sexy": 0.41, "image:porn": 0.01},
                "audit_decision": "allow",
                "audit_reason": "default",
                "proxy_blocked": False,
            }
        ]
    }
    current = {
        "images": [
            {
                "name": "sample.avif",
                "classifier_scores": {"image:sexy": 0.61, "image:porn": 0.01},
                "audit_decision": "block",
                "audit_reason": "classifier:image:sexy",
                "proxy_blocked": True,
            }
        ]
    }

    comparison = regression.compare_runs(baseline, current, tolerance=0.02)

    assert comparison["summary"]["decision_changes"] == 1
    assert comparison["summary"]["score_changes"] == 1
    image = comparison["images"][0]
    assert image["comparison"]["decision_changed"] is True
    assert image["comparison"]["score_deltas"][0]["label"] == "image:sexy"


def test_compare_runs_treats_small_drift_as_same() -> None:
    baseline = {
        "images": [
            {
                "name": "sample.avif",
                "classifier_scores": {"image:sexy": 0.41},
                "audit_decision": "allow",
                "audit_reason": "default",
                "proxy_blocked": False,
            }
        ]
    }
    current = {
        "images": [
            {
                "name": "sample.avif",
                "classifier_scores": {"image:sexy": 0.419},
                "audit_decision": "allow",
                "audit_reason": "default",
                "proxy_blocked": False,
            }
        ]
    }

    comparison = regression.compare_runs(baseline, current, tolerance=0.02)

    assert comparison["summary"]["decision_changes"] == 0
    assert comparison["summary"]["score_changes"] == 0
    assert comparison["images"][0]["comparison"]["status"] == "same"


def test_render_html_report_contains_changed_state(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.avif"
    image_path.write_bytes(b"fake")

    report = {
        "generated_at": "2026-05-24T00:00:00Z",
        "proxy_url": "http://127.0.0.1:8080",
        "api_url": "http://127.0.0.1:8000",
        "images": [
            {
                "name": "sample.avif",
                "path": str(image_path),
                "audit_decision": "block",
                "audit_reason": "classifier:image:sexy",
                "proxy_blocked": True,
                "classifier_scores": {"image:sexy": 0.93},
            }
        ],
        "comparison": {
            "summary": {
                "decision_changes": 1,
                "score_changes": 1,
                "removed_images": 0,
            },
            "images": [
                {
                    "name": "sample.avif",
                    "path": str(image_path),
                    "audit_decision": "block",
                    "audit_reason": "classifier:image:sexy",
                    "proxy_blocked": True,
                    "classifier_scores": {"image:sexy": 0.93},
                    "comparison": {
                        "status": "changed",
                        "score_deltas": [
                            {
                                "label": "image:sexy",
                                "baseline": 0.40,
                                "current": 0.93,
                                "delta": 0.53,
                            }
                        ],
                    },
                }
            ],
        },
    }

    html = regression.render_html_report(report, report_dir=tmp_path)

    assert "Image decisions and score drift" in html
    assert "sample.avif" in html
    assert "comparison: <strong>changed</strong>" in html
    assert "0.4000 → 0.9300 (+0.5300)" in html
    assert "preview hidden" in html
    assert "<img src=" not in html


def test_render_html_report_shows_preview_for_allowed_image(tmp_path: Path) -> None:
    image_path = tmp_path / "allowed.avif"
    image_path.write_bytes(b"fake")

    report = {
        "generated_at": "2026-05-24T00:00:00Z",
        "proxy_url": "http://127.0.0.1:8080",
        "api_url": "http://127.0.0.1:8000",
        "images": [
            {
                "name": "allowed.avif",
                "path": str(image_path),
                "audit_decision": "allow",
                "audit_reason": "default",
                "proxy_blocked": False,
                "classifier_scores": {"image:drawings": 0.93},
            }
        ],
        "comparison": {
            "summary": {
                "decision_changes": 0,
                "score_changes": 0,
                "removed_images": 0,
            },
            "images": [
                {
                    "name": "allowed.avif",
                    "path": str(image_path),
                    "audit_decision": "allow",
                    "audit_reason": "default",
                    "proxy_blocked": False,
                    "classifier_scores": {"image:drawings": 0.93},
                    "comparison": {
                        "status": "same",
                        "score_deltas": [],
                    },
                }
            ],
        },
    }

    html = regression.render_html_report(report, report_dir=tmp_path)

    assert '<img src="allowed.avif" alt="allowed.avif" loading="lazy" />' in html
    assert "preview hidden" not in html
