"""``seenoevil-backup`` — local backup/restore CLI for the policy DB.

The pod's persistent state is small: a SQLite DB and a CA directory. This
script tarballs both into ``backup.local_path`` (configured in ``config.yaml``)
and prunes by ``backup.retention``. Restore reads a tarball back over the
same paths after refusing to clobber a different DB hostname.

Usage::

    seenoevil-backup snapshot          # one-shot snapshot, prune old files
    seenoevil-backup list              # list snapshots in backup.local_path
    seenoevil-backup restore <path>    # restore from a tarball

For continuous replication (instead of point-in-time snapshots), enable
Litestream via ``deploy/compose --profile litestream``; this CLI is the
"works without any extra service" baseline.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tarfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig, BackupConfig, load_config

log = logging.getLogger("seenoevil_api.backup")

# Files that make up the persistent state of the pod. Missing entries are
# silently skipped so this works whether or not first-start has populated
# every directory.
_BACKUP_PATHS: tuple[str, ...] = (
    "policy.db",
    "policy.db-wal",
    "policy.db-shm",
    "ca",
    "models",  # Models can be re-fetched but checksums are pinned; saves bandwidth.
)


def _data_dir(config: AppConfig) -> Path:
    return Path(config.pod.data_dir)


def _sqlite_path(config: AppConfig) -> Path | None:
    url = config.db.url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix) :])


def _candidate_files(data_dir: Path, db_path: Path | None) -> Iterable[Path]:
    seen: set[Path] = set()
    for rel in _BACKUP_PATHS:
        p = data_dir / rel
        if p.exists() and p not in seen:
            seen.add(p)
            yield p
    if db_path is not None and db_path.exists() and db_path not in seen:
        seen.add(db_path)
        yield db_path


def snapshot(config: AppConfig) -> Path:
    """Create one snapshot tarball; return its path."""
    cfg = config.backup
    out_dir = Path(cfg.local_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"seenoevil-{stamp}.tar.gz"

    data_dir = _data_dir(config)
    db_path = _sqlite_path(config)
    files = list(_candidate_files(data_dir, db_path))
    if not files:
        raise FileNotFoundError(f"no backup-eligible files found under {data_dir}")
    with tarfile.open(out_path, "w:gz") as tar:
        for f in files:
            arcname = f.relative_to(data_dir) if data_dir in f.parents or f == data_dir else f.name
            tar.add(f, arcname=str(arcname))
    log.info("wrote snapshot %s (%d files)", out_path, len(files))
    prune(cfg)
    return out_path


def list_snapshots(cfg: BackupConfig) -> list[Path]:
    out_dir = Path(cfg.local_path)
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob("seenoevil-*.tar.gz"))


def prune(cfg: BackupConfig) -> list[Path]:
    """Delete the oldest snapshots beyond ``cfg.retention``. Returns deleted paths."""
    if cfg.retention <= 0:
        return []
    snaps = list_snapshots(cfg)
    excess = snaps[: max(0, len(snaps) - cfg.retention)]
    for p in excess:
        try:
            p.unlink()
            log.info("pruned old snapshot %s", p)
        except OSError as exc:  # pragma: no cover - filesystem dependent
            log.warning("failed to prune %s: %s", p, exc)
    return excess


def restore(config: AppConfig, archive: Path) -> None:
    """Extract ``archive`` over ``config.pod.data_dir``.

    The DB engine must be stopped before calling restore; this CLI is intended
    for ``docker compose run`` style invocations, not in-process.
    """
    if not archive.exists():
        raise FileNotFoundError(archive)
    data_dir = _data_dir(config)
    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        # Defence in depth: refuse absolute paths and traversal.
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ValueError(f"refusing unsafe path in archive: {member.name!r}")
        tar.extractall(data_dir)  # noqa: S202 - guarded above
    log.info("restored %s into %s", archive, data_dir)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seenoevil-backup",
        description="see-no-evil — backup / restore the policy DB and CA",
    )
    p.add_argument(
        "--config",
        help="path to config.yaml (defaults to $SEENOEVIL_CONFIG / built-in defaults)",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot", help="create a snapshot tarball")
    sub.add_parser("list", help="list snapshots in backup.local_path")
    r_p = sub.add_parser("restore", help="restore from a snapshot tarball")
    r_p.add_argument("archive", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    config = load_config(args.config) if args.config else load_config()

    if args.command == "snapshot":
        try:
            out = snapshot(config)
        except FileNotFoundError as exc:
            print(f"snapshot failed: {exc}", file=sys.stderr)
            return 2
        print(out)
        return 0

    if args.command == "list":
        for p in list_snapshots(config.backup):
            print(p)
        return 0

    if args.command == "restore":
        try:
            restore(config, args.archive)
        except (FileNotFoundError, ValueError) as exc:
            print(f"restore failed: {exc}", file=sys.stderr)
            return 2
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
