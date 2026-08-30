"""First-run install wizard for see-no-evil.

Run with:
    python -m seenoevil_api.wizard
or via the ``seenoevil-setup`` entry-point installed by pyproject.toml.

The wizard:
1. Creates / migrates the database.
2. Prompts for admin e-mail and password.
3. Prompts for DNS upstream preference.
4. Writes a minimal ``config.yaml`` to the data directory (if none exists).
5. Seeds the admin account in the database.

All prompts respect pre-set environment variables so the wizard can run
non-interactively (e.g. inside a container with env vars injected by compose).
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from textwrap import dedent

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_CONFIG_PATH = "SEENOEVIL_CONFIG"
ENV_INITIAL_ADMIN_EMAIL = "SEENOEVIL_INITIAL_ADMIN_EMAIL"
ENV_INITIAL_ADMIN_PASSWORD = "SEENOEVIL_INITIAL_ADMIN_PASSWORD"
ENV_DATA_DIR = "SEENOEVIL_DATA_DIR"

UPSTREAM_PRESETS: dict[str, list[str]] = {
    "cloudflare-family": [
        "https://1.1.1.3/dns-query",
        "https://1.0.0.3/dns-query",
    ],
    "cloudflare": [
        "https://1.1.1.1/dns-query",
        "https://1.0.0.1/dns-query",
    ],
    "quad9": [
        "https://dns.quad9.net/dns-query",
    ],
    "google": [
        "https://dns.google/dns-query",
    ],
}

UPSTREAM_DESCRIPTIONS: dict[str, str] = {
    "cloudflare-family": "Cloudflare for Families (blocks malware + adult content at DNS level)",
    "cloudflare": "Cloudflare (fast, privacy-preserving, no content filtering)",
    "quad9": "Quad9 (blocks malware only)",
    "google": "Google (no content filtering)",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt(msg: str, default: str | None = None, password: bool = False) -> str:
    """Prompt the user; honour env-var or provided default."""
    display = (f"{msg} [{default}]: " if not password else f"{msg}: ") if default else f"{msg}: "

    value = getpass.getpass(display).strip() if password else input(display).strip()

    return value or (default or "")


def _prompt_choice(msg: str, choices: list[str], default: str) -> str:
    """Prompt for a fixed choice."""
    numbered = "\n".join(
        f"  {i + 1}. {c} — {UPSTREAM_DESCRIPTIONS.get(c, '')}" for i, c in enumerate(choices)
    )
    while True:
        print(f"\n{msg}")
        print(numbered)
        raw = input(f"Enter 1-{len(choices)} [default: {default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("  Invalid choice, please try again.")


def _confirm(msg: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(msg + suffix).strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------


def _generate_token() -> str:
    import secrets as _secrets

    return _secrets.token_hex(32)


def _build_config(
    data_dir: str,
    admin_email: str,
    dns_upstream: str,
    hostname: str,
    proxy_token: str | None = None,
    scanner_token: str | None = None,
) -> dict:
    if not proxy_token:
        proxy_token = os.environ.get("SEENOEVIL_PROXY_TOKEN") or _generate_token()
    if not scanner_token:
        scanner_token = os.environ.get("SCANNER_API_TOKEN") or _generate_token()
    return {
        "pod": {
            "hostname": hostname,
            "data_dir": data_dir,
        },
        "db": {
            "url": f"sqlite:///{data_dir}/policy.db",
        },
        "cache": {"kind": "embedded"},
        "auth": {
            "builtin": {"admin_email": admin_email},
        },
        "dns": {
            "upstreams": UPSTREAM_PRESETS[dns_upstream],
        },
        "proxy": {
            "api_token": proxy_token,
        },
        "scanner": {
            "api_token": scanner_token,
        },
        "profiles": [
            {
                "name": "kids",
                "description": "Strict filtering — appropriate for children",
                "notify_on_block": True,
            },
            {
                "name": "adults",
                "description": "No content filtering",
            },
            {
                "name": "guests",
                "description": "Standard filtering",
            },
        ],
        "devices": {
            "default_profile": "guests",
            "static": [],
        },
    }


# ---------------------------------------------------------------------------
# Database seeding
# ---------------------------------------------------------------------------


def _seed_admin(db_url: str, email: str, password: str) -> None:
    """Run migrations and seed the admin account."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from .auth import admin_is_configured, set_admin_password
    from .migrations import upgrade_to_head

    upgrade_to_head(db_url)
    engine = create_engine(db_url, future=True)

    with Session(engine, autoflush=False, autocommit=False) as session:
        if admin_is_configured(session):
            print("  Admin account already configured — skipping password seed.")
            return
        set_admin_password(session, email, password)
        session.commit()
    print(f"  Admin account created: {email}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(
        dedent("""
    ╔══════════════════════════════════════════════╗
    ║       see-no-evil  —  first-run setup        ║
    ╚══════════════════════════════════════════════╝
    """)
    )

    # ----------------------------------------------------------------
    # 1. Determine data / config paths
    # ----------------------------------------------------------------
    data_dir = os.environ.get(ENV_DATA_DIR, "/data")
    config_path_env = os.environ.get(ENV_CONFIG_PATH)

    config_path = Path(config_path_env) if config_path_env else Path(data_dir) / "config.yaml"

    config_exists = config_path.exists()

    if config_exists:
        print(f"Config found: {config_path}")
        if not _confirm("A config already exists. Overwrite it?", default=False):
            print("  Keeping existing config.")
        else:
            config_exists = False  # will regenerate below

    # ----------------------------------------------------------------
    # 2. Gather inputs
    # ----------------------------------------------------------------
    hostname_default = "seenoevil.lan"
    admin_email_default = "admin@example.local"

    hostname = os.environ.get("SNE_HOSTNAME") or _prompt(
        "Admin UI hostname", default=hostname_default
    )

    admin_email = os.environ.get(ENV_INITIAL_ADMIN_EMAIL) or _prompt(
        "Admin e-mail", default=admin_email_default
    )

    # Password
    admin_password = os.environ.get(ENV_INITIAL_ADMIN_PASSWORD, "")
    if not admin_password:
        while True:
            p1 = _prompt("Admin password (min 8 chars)", password=True)
            p2 = _prompt("Confirm password", password=True)
            if p1 == p2 and len(p1) >= 8:
                admin_password = p1
                break
            if p1 != p2:
                print("  Passwords do not match. Try again.")
            else:
                print("  Password must be at least 8 characters.")

    # DNS upstream
    dns_upstream = _prompt_choice(
        "Choose DNS upstream",
        choices=list(UPSTREAM_PRESETS.keys()),
        default="cloudflare-family",
    )

    # Proxy/scanner tokens — generate if not supplied via env so a fresh
    # install never bricks filtering due to empty token + fail_closed:true.
    proxy_token = os.environ.get("SEENOEVIL_PROXY_TOKEN") or ""
    scanner_token = os.environ.get("SCANNER_API_TOKEN") or ""
    if not proxy_token:
        proxy_token = _generate_token()
        print("  Generated proxy token (written to config.yaml)")
        print("  Also set SEENOEVIL_PROXY_TOKEN env for compose deployments.")
    if not scanner_token:
        scanner_token = _generate_token()

    # ----------------------------------------------------------------
    # 3. Write config.yaml
    # ----------------------------------------------------------------
    if not config_exists:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = _build_config(
            data_dir=data_dir,
            admin_email=admin_email,
            dns_upstream=dns_upstream,
            hostname=hostname,
            proxy_token=proxy_token,
            scanner_token=scanner_token,
        )
        with config_path.open("w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        print(f"\nConfig written to {config_path}")
    else:
        print(f"\nUsing existing config at {config_path}")

    # ----------------------------------------------------------------
    # 4. Seed the database
    # ----------------------------------------------------------------
    print("\nInitialising database …")
    try:
        from .config import load_config

        app_cfg = load_config(config_path)
        _seed_admin(app_cfg.db.url, admin_email, admin_password)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # ----------------------------------------------------------------
    # 5. Done
    # ----------------------------------------------------------------
    print(
        dedent(f"""
    ✓  Setup complete.

    Next steps:
      1. Start the stack:
           docker compose --profile core up -d

      2. Browse to  https://{hostname}
         Log in with  {admin_email}

      3. Install the MITM CA certificate on each device you want to filter.
         (Download from  https://{hostname}/v1/proxy/ca.crt )
    """)
    )


if __name__ == "__main__":
    main()
