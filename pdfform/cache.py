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
from functools import lru_cache
from importlib import metadata

MAX_ENTRIES = 1000
MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


#: Bump whenever a fill/validation change can alter engine output for a
#: payload that hashes the same (e.g. the 2026-07 AcroForm radio-group /V
#: fix). Folding this into the key means stale on-disk entries from before
#: the change are never served -- they simply become unreachable under the
#: new key and age out through the normal LRU eviction, with no need to
#: reach into the deployed cache directory by hand.
#: v3 (2026-07-16): is_new_application's mg.app checkbox pair was ticking
#: the wrong box (see adapt_form956.py) -- any cached PDF for a payload
#: that set this field was wrong and must not be served after the fix.
#: v4 (2026-09-13): cost agreements cached before b237b60 ("fixing signature
#: stamp", which moved the content frame up to reserve the post-signing stamp
#: band) kept being served in the old layout. Signing one of those put the
#: per-page stamp on top of body text, or failed outright because its
#: signature box sat inside the band.
CACHE_SCHEMA_VERSION = 4

# --------------------------------------------------------------------- build fingerprint
# CACHE_SCHEMA_VERSION only helps when someone remembers to bump it, and the
# log above is four occasions where nobody did until stale PDFs had already
# gone out. Most recently (2026-11-26) the divorce builder learned a
# variable-length payment schedule, and a request identical to one made
# before the deploy kept getting the old three-row PDF back from the
# persistent cache volume.
#
# So the key is also salted with a fingerprint of everything that decides
# what a PDF looks like: the source of every builder and the fill engine,
# the form YAMLs, the template PDFs, the brand assets, and the versions of
# the two PDF libraries. Any deploy that changes one of them starts on a
# fresh key space automatically; a deploy that changes nothing else (docs,
# tests, CI) keeps its cache warm. Old entries are never deleted by hand:
# they become unreachable and age out through the normal LRU eviction.
#
# Content, not mtimes or a git SHA: mtimes change on every checkout, and a
# git SHA changes on README edits. CACHE_SCHEMA_VERSION stays as the manual
# override for anything this can't see (e.g. a system font change).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINGERPRINT_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pdfform", (".py",)),
    ("costagreements", (".py", ".png", ".jpg", ".jpeg", ".ttf", ".otf")),
    ("forms", (".yaml", ".yml")),
    ("pdfs", (".pdf",)),
)
FINGERPRINT_LIBRARIES = ("reportlab", "pymupdf")


def compute_build_fingerprint(root: str = _ROOT,
                              sources=FINGERPRINT_SOURCES,
                              libraries=FINGERPRINT_LIBRARIES) -> str:
    """SHA-256 over the relative path + bytes of every output-shaping file
    under ``root`` (sorted, so the walk order can't change it), plus the
    installed versions of ``libraries``. Line endings are normalised so a
    Windows checkout and the Linux container agree."""
    h = hashlib.sha256()
    for sub, exts in sources:
        base = os.path.join(root, sub)
        files = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in filenames:
                if fn.lower().endswith(exts):
                    files.append(os.path.join(dirpath, fn))
        for path in sorted(files):
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as f:
                data = f.read()
            if rel.endswith((".py", ".yaml", ".yml")):
                data = data.replace(b"\r\n", b"\n")
            h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    for lib in libraries:
        try:
            version = metadata.version(lib)
        except metadata.PackageNotFoundError:
            version = "absent"
        h.update(f"{lib}={version}\0".encode("utf-8"))
    return h.hexdigest()


@lru_cache(maxsize=1)
def build_fingerprint() -> str:
    """The running build's fingerprint, computed once per process."""
    return compute_build_fingerprint()


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
    schema version and the build fingerprint (see above), so a PDF built
    by older code is never served after a deploy that changes the output."""
    salted = f"{CACHE_SCHEMA_VERSION}:{build_fingerprint()}:{canonicalise(payload)}"
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
