from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
import zstandard as zstd

import snapshot_bootstrap as sb


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _make_db(path: Path, rows: int = 2) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY)")
        for i in range(rows):
            conn.execute("INSERT INTO decisions (decision_id) VALUES (?)", (f"did-{i}",))
        conn.commit()
    finally:
        conn.close()


def _compressed_db(tmp_path: Path, rows: int = 2) -> tuple[bytes, str]:
    db = tmp_path / "source.db"
    _make_db(db, rows=rows)
    compressed = zstd.ZstdCompressor(level=1).compress(db.read_bytes())
    return compressed, hashlib.sha256(compressed).hexdigest()


def test_bootstrap_sqlite_snapshot_downloads_verifies_and_installs(tmp_path, monkeypatch):
    compressed, sha = _compressed_db(tmp_path, rows=3)
    manifest = {
        "snapshot": {
            "date": "2026-05-31",
            "sqlite_zst": {
                "path": "artifacts/sqlite/snapshots/2026-05-31.decisions.sqlite.zst",
                "sha256": sha,
                "bytes": len(compressed),
            },
            "rows": 3,
        },
    }

    def fake_urlopen(url: str):
        if url.endswith(sb.MANIFEST_PATH):
            return _Response(json.dumps(manifest).encode("utf-8"))
        if url.endswith(".sqlite.zst"):
            return _Response(compressed)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(sb.urllib.request, "urlopen", fake_urlopen)

    result = sb.bootstrap_sqlite_snapshot(data_dir=tmp_path)

    assert result.downloaded is True
    assert result.rows == 3
    assert result.sha256 == sha
    assert (tmp_path / "decisions.db").exists()
    assert sb._sqlite_row_count(tmp_path / "decisions.db") == 3

    sidecar = json.loads((tmp_path / "decisions.db.snapshot.json").read_text(encoding="utf-8"))
    assert sidecar["snapshot"]["sqlite_zst"]["sha256"] == sha


def test_bootstrap_sqlite_snapshot_skips_existing_db_without_force(tmp_path, monkeypatch):
    _make_db(tmp_path / "decisions.db", rows=1)

    def fail_urlopen(url: str):
        raise AssertionError(f"urlopen should not be called: {url}")

    monkeypatch.setattr(sb.urllib.request, "urlopen", fail_urlopen)

    result = sb.bootstrap_sqlite_snapshot(data_dir=tmp_path)

    assert result.downloaded is False
    assert result.rows == 1


def test_bootstrap_sqlite_snapshot_rejects_hash_mismatch_and_cleans_tmp(tmp_path, monkeypatch):
    compressed, _sha = _compressed_db(tmp_path, rows=1)
    manifest = {
        "snapshot": {
            "sqlite_zst": {
                "path": "artifacts/sqlite/snapshots/bad.sqlite.zst",
                "sha256": "0" * 64,
            },
        },
    }

    def fake_urlopen(url: str):
        if url.endswith(sb.MANIFEST_PATH):
            return _Response(json.dumps(manifest).encode("utf-8"))
        return _Response(compressed)

    monkeypatch.setattr(sb.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        sb.bootstrap_sqlite_snapshot(data_dir=tmp_path)

    assert not (tmp_path / "decisions.db").exists()
    assert not (tmp_path / "decisions.db.tmp").exists()
