"""Persistent content-addressed cache for PDF analysis and embeddings."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Iterable


def _cache_path() -> str:
    return os.getenv(
        "ANALYSIS_CACHE_PATH",
        os.path.join(os.getenv("DATA_DIR", "./data"), "analysis_cache.sqlite3"),
    )


def _connect() -> sqlite3.Connection:
    path = _cache_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS analysis_cache ("
        "kind TEXT NOT NULL, cache_key TEXT NOT NULL, payload TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(kind, cache_key))"
    )
    return connection


def get_json(kind: str, cache_key: str) -> Any:
    path = _cache_path()
    if not os.path.exists(path):
        return None
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM analysis_cache WHERE kind=? AND cache_key=?",
            (kind, cache_key),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def get_many_json(kind: str, cache_keys: Iterable[str]) -> Dict[str, Any]:
    keys = list(dict.fromkeys(str(key) for key in cache_keys if key))
    path = _cache_path()
    if not keys or not os.path.exists(path):
        return {}
    found: Dict[str, Any] = {}
    with _connect() as connection:
        for start in range(0, len(keys), 500):
            batch = keys[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"SELECT cache_key, payload FROM analysis_cache WHERE kind=? AND cache_key IN ({placeholders})",
                [kind, *batch],
            ).fetchall()
            for key, payload in rows:
                try:
                    found[key] = json.loads(payload)
                except (TypeError, ValueError):
                    continue
    return found


def set_json(kind: str, cache_key: str, payload: Any) -> None:
    set_many_json(kind, {cache_key: payload})


def set_many_json(kind: str, values: Dict[str, Any]) -> None:
    rows = [
        (kind, str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        for key, value in values.items()
        if key
    ]
    if not rows:
        return
    with _connect() as connection:
        connection.executemany(
            "INSERT INTO analysis_cache(kind, cache_key, payload) VALUES(?,?,?) "
            "ON CONFLICT(kind, cache_key) DO UPDATE SET payload=excluded.payload, created_at=CURRENT_TIMESTAMP",
            rows,
        )
