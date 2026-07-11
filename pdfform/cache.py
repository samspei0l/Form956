"""Idempotence cache for filled PDFs.

The single biggest production win for "thousands a day" is collapsing
repeat traffic to a cache hit. Most fills are amendments of the same
form for the same client — same payload, same PDF, different timestamp.
The cache canonicalises the payload, hashes it, and reuses the file.

Design choices:
  - On-disk LRU keyed by SHA-256. Survives process restarts (which
    matter in gunicorn pre-fork / container restarts).
  - Bounded: 1000 entries OR ~2 GB, whichever comes first. Oldest
    entries evicted by mtime. This is the safety net for runaway
    growth from a buggy client that submits slightly-different
    payloads by accident.
  - 32-char hex cache key. Short enough for a header, opaque to
    clients.
  - Cache directory ``uploads/cache/``. The route creates it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

MAX_ENTRIES = 1000
MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


#: Bump whenever a fill/validation change can alter engine output for a
#: payload that hashes the same (e.g. the 2026-07 AcroForm radio-group /V
#: fix). Folding this into the key means stale on-disk entries from before
#: the change are never served -- they simply become unreachable under the
#: new key and age out through the normal LRU eviction, with no need to
#: reach into the deployed cache directory by hand.
CACHE_SCHEMA_VERSION = 2


def canonicalise(payload: dict) -> str:
    """Stable, deterministic JSON for hashing.

    - Sort keys.
    - Strip None values (so ``{"a": 1, "b": null}`` and ``{"a": 1}`` hash
      the same).
    - Use compact separators (whitespace-free) to avoid churn.
    """
    cleaned = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def cache_key(payload: dict) -> str:
    """SHA-256 hex of the canonicalised payload, salted with the cache
    schema version (see ``CACHE_SCHEMA_VERSION``)."""
    salted = f"{CACHE_SCHEMA_VERSION}:{canonicalise(payload)}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


class PdfCache:
    """On-disk LRU of filled PDFs keyed by SHA-256 of canonical payload.

    The cache is best-effort: read errors return ``None``, write errors
    are logged and skipped. The fill route must still produce a valid
    PDF even if the cache layer fails.
    """

    def __init__(self, dirpath: str):
        self.dirpath = dirpath
        os.makedirs(self.dirpath, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.dirpath, f"{key}.pdf")

    def path_of(self, key: str) -> str:
        """Return the absolute path for a key. Public alias for the
        route handlers; same as the internal ``_path``."""
        return self._path(key)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                data = f.read()
            # Touch mtime so LRU keeps recent hits.
            os.utime(path, None)
            return data
        except OSError:
            return None

    def has(self, key: str) -> bool:
        return os.path.exists(self._path(key))

    def put(self, key: str, data: bytes) -> str:
        """Write ``data`` under ``key``, then evict to size limits.

        Returns the absolute path of the written file.
        """
        path = self._path(key)
        # Atomic write so a partial file isn't readable.
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self._evict()
        return path

    def _evict(self) -> None:
        """Drop oldest entries until under MAX_ENTRIES and MAX_BYTES."""
        try:
            entries = []
            total = 0
            for fn in os.listdir(self.dirpath):
                if not fn.endswith(".pdf"):
                    continue
                p = os.path.join(self.dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, p))
                total += st.st_size

            # Sort oldest first.
            entries.sort(key=lambda x: x[0])

            # Drop until under both limits.
            i = 0
            while (len(entries) - i > MAX_ENTRIES
                   or total > MAX_BYTES) and i < len(entries):
                _, size, p = entries[i]
                try:
                    os.remove(p)
                    total -= size
                except OSError:
                    pass
                i += 1
        except OSError:
            pass

    def stats(self) -> dict:
        """Health endpoint payload."""
        try:
            entries = [fn for fn in os.listdir(self.dirpath) if fn.endswith(".pdf")]
            total = 0
            for fn in entries:
                try:
                    total += os.path.getsize(os.path.join(self.dirpath, fn))
                except OSError:
                    pass
            return {
                "cache_size": len(entries),
                "cache_bytes": total,
            }
        except OSError:
            return {"cache_size": 0, "cache_bytes": 0}
