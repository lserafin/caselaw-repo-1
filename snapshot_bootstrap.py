"""Bootstrap a local decisions.db from a published SQLite snapshot.

The MCP server reads an uncompressed SQLite database from
``SWISS_CASELAW_DIR/decisions.db``. Hugging Face publishes the bootstrap
artifact as a zstd-compressed file referenced by ``artifacts/manifest.json``.
This module bridges that gap without rebuilding the DB from Parquet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HF_REPO = "voilaj/swiss-caselaw"
DEFAULT_REVISION = "main"
MANIFEST_PATH = "artifacts/manifest.json"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    db_path: Path
    downloaded: bool
    rows: int | None
    snapshot_path: str | None
    sha256: str | None


class _HashingReader:
    def __init__(self, raw, digest):
        self.raw = raw
        self.digest = digest

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        if data:
            self.digest.update(data)
        return data


def _repo_base_url(repo_id: str, revision: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}"


def _read_json_url(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _validate_snapshot_entry(manifest: dict[str, Any]) -> dict[str, Any]:
    snapshot = manifest.get("snapshot")
    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "No SQLite snapshot is advertised in artifacts/manifest.json; "
            "fall back to the Parquet update_database path."
        )
    sqlite_zst = snapshot.get("sqlite_zst")
    if not isinstance(sqlite_zst, dict):
        raise RuntimeError("Manifest snapshot is missing sqlite_zst metadata.")
    for key in ("path", "sha256"):
        if not sqlite_zst.get(key):
            raise RuntimeError(f"Manifest snapshot.sqlite_zst is missing {key!r}.")
    return snapshot


def _sqlite_row_count(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        has_decisions = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'decisions'"
        ).fetchone()
        if not has_decisions:
            raise RuntimeError("Downloaded SQLite snapshot has no decisions table.")
        rows = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        conn.execute("SELECT decision_id FROM decisions LIMIT 1").fetchone()
        return int(rows)
    finally:
        conn.close()


def bootstrap_sqlite_snapshot(
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
    repo_id: str = DEFAULT_HF_REPO,
    revision: str = DEFAULT_REVISION,
    force: bool = False,
) -> BootstrapResult:
    """Download, verify, decompress, and install the advertised snapshot.

    If ``decisions.db`` already exists and ``force`` is false, this is a
    no-op. That makes it safe to pass from normal MCP startup flags.
    """
    resolved_data_dir = (
        data_dir
        if data_dir is not None
        else Path(os.environ.get("SWISS_CASELAW_DIR", Path.home() / ".swiss-caselaw"))
    ).expanduser()
    final_db = (db_path or resolved_data_dir / "decisions.db").expanduser()

    if final_db.exists() and not force:
        rows = _sqlite_row_count(final_db)
        log.info("Using existing SQLite DB at %s (%d rows)", final_db, rows)
        return BootstrapResult(
            db_path=final_db,
            downloaded=False,
            rows=rows,
            snapshot_path=None,
            sha256=None,
        )

    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    final_db.parent.mkdir(parents=True, exist_ok=True)
    base_url = _repo_base_url(repo_id, revision)
    manifest = _read_json_url(f"{base_url}/{MANIFEST_PATH}")
    snapshot = _validate_snapshot_entry(manifest)
    meta = snapshot["sqlite_zst"]
    snapshot_rel_path = str(meta["path"])
    expected_sha = str(meta["sha256"])

    tmp_db = final_db.with_suffix(final_db.suffix + ".tmp")
    tmp_db.unlink(missing_ok=True)

    log.info("Downloading SQLite snapshot %s from %s", snapshot_rel_path, repo_id)
    digest = hashlib.sha256()
    try:
        try:
            import zstandard as zstd
        except ImportError as e:
            raise RuntimeError(
                "The zstandard package is required for SQLite snapshot bootstrap. "
                "Install it with `pip install zstandard`."
            ) from e

        with urllib.request.urlopen(f"{base_url}/{snapshot_rel_path}") as src:
            with tmp_db.open("wb") as dst:
                zstd.ZstdDecompressor().copy_stream(_HashingReader(src, digest), dst)

        actual_sha = digest.hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"SQLite snapshot SHA-256 mismatch: got {actual_sha}, "
                f"expected {expected_sha}"
            )

        rows = _sqlite_row_count(tmp_db)
        expected_rows = snapshot.get("rows") or snapshot.get("decisions_count")
        if expected_rows is not None and rows != int(expected_rows):
            raise RuntimeError(
                f"SQLite snapshot row-count mismatch: got {rows}, "
                f"expected {expected_rows}"
            )

        tmp_db.replace(final_db)
        sidecar = final_db.with_suffix(final_db.suffix + ".snapshot.json")
        sidecar.write_text(
            json.dumps(
                {
                    "repo_id": repo_id,
                    "revision": revision,
                    "snapshot": snapshot,
                    "installed_db": str(final_db),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        log.info("Installed SQLite snapshot at %s (%d rows)", final_db, rows)
        return BootstrapResult(
            db_path=final_db,
            downloaded=True,
            rows=rows,
            snapshot_path=snapshot_rel_path,
            sha256=actual_sha,
        )
    except Exception:
        tmp_db.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap SWISS_CASELAW_DIR/decisions.db from Hugging Face."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SWISS_CASELAW_DIR", Path.home() / ".swiss-caselaw")),
        help="Directory containing decisions.db (default: SWISS_CASELAW_DIR or ~/.swiss-caselaw)",
    )
    parser.add_argument("--repo-id", default=DEFAULT_HF_REPO)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace decisions.db even if it already exists.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = bootstrap_sqlite_snapshot(
        data_dir=args.data_dir,
        repo_id=args.repo_id,
        revision=args.revision,
        force=args.force,
    )
    action = "downloaded" if result.downloaded else "already-present"
    print(f"{action}: {result.db_path} ({result.rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
