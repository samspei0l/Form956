"""The cache key carries a fingerprint of the code and templates that shape a
PDF, so a deploy that changes the output never serves PDFs built by the old
code from the persistent cache.

The bug this exists for (2026-11-26): after the divorce builder learned a
variable-length payment schedule, a request identical to one made before the
deploy kept returning the old three-row PDF, because the cache key depended
only on the payload and a manually-bumped CACHE_SCHEMA_VERSION.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdfform import cache as C  # noqa: E402


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "costagreements" / "builders").mkdir(parents=True)
    (tmp_path / "costagreements" / "assets").mkdir()
    (tmp_path / "pdfform").mkdir()
    (tmp_path / "forms").mkdir()
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "costagreements" / "builders" / "divorce.py").write_bytes(b"ROWS = 3\n")
    (tmp_path / "costagreements" / "assets" / "logo.png").write_bytes(b"\x89PNG-1")
    (tmp_path / "pdfform" / "engine.py").write_bytes(b"x = 1\n")
    (tmp_path / "forms" / "form956.yaml").write_bytes(b"id: form956\n")
    (tmp_path / "pdfs" / "956.pdf").write_bytes(b"%PDF-1")
    (tmp_path / "README.md").write_bytes(b"readme\n")
    (tmp_path / "tests" / "test_x.py").write_bytes(b"def test(): pass\n")
    return tmp_path


def fp(root, libraries=()):
    return C.compute_build_fingerprint(str(root), libraries=libraries)


def test_same_tree_same_fingerprint(tree):
    assert fp(tree) == fp(tree)


@pytest.mark.parametrize("rel, content", [
    ("costagreements/builders/divorce.py", b"ROWS = 'any'\n"),   # a builder
    ("pdfform/engine.py", b"x = 2\n"),                            # the fill engine
    ("forms/form956.yaml", b"id: form956\nnew: field\n"),          # a form map
    ("pdfs/956.pdf", b"%PDF-2"),                                  # a template PDF
    ("costagreements/assets/logo.png", b"\x89PNG-2"),             # brand asset
    ("costagreements/builders/new_doc.py", b"y = 1\n"),           # a new builder
])
def test_anything_that_shapes_a_pdf_changes_it(tree, rel, content):
    before = fp(tree)
    (tree / rel).write_bytes(content)
    assert fp(tree) != before


@pytest.mark.parametrize("rel", ["README.md", "tests/test_x.py"])
def test_docs_and_tests_do_not(tree, rel):
    before = fp(tree)
    (tree / rel).write_bytes(b"changed\n")
    assert fp(tree) == before


def test_line_endings_do_not(tree):
    before = fp(tree)
    (tree / "costagreements" / "builders" / "divorce.py").write_bytes(b"ROWS = 3\r\n")
    assert fp(tree) == before


def test_pycache_is_ignored(tree):
    before = fp(tree)
    (tree / "pdfform" / "__pycache__").mkdir()
    (tree / "pdfform" / "__pycache__" / "engine.cpython-312.py").write_bytes(b"junk")
    assert fp(tree) == before


def test_library_version_is_part_of_it(tree, monkeypatch):
    monkeypatch.setattr(C.metadata, "version", lambda name: "1.0")
    a = fp(tree, ("reportlab",))
    monkeypatch.setattr(C.metadata, "version", lambda name: "2.0")
    assert fp(tree, ("reportlab",)) != a


def test_cache_key_changes_with_the_build(monkeypatch):
    payload = {"client_name": "John Citizen", "rows": [1, 2, 3]}
    monkeypatch.setattr(C, "build_fingerprint", lambda: "old-build")
    old = C.cache_key(payload)
    monkeypatch.setattr(C, "build_fingerprint", lambda: "new-build")
    assert C.cache_key(payload) != old
    assert C.cache_key(payload) == C.cache_key(dict(payload))  # still deterministic


def test_real_fingerprint_is_stable_and_hex():
    a, b = C.compute_build_fingerprint(), C.compute_build_fingerprint()
    assert a == b and len(a) == 64 and int(a, 16) >= 0


def test_health_reports_the_build(tmp_path_factory):
    import importlib
    import os

    os.environ["UPLOADS_DIR"] = str(tmp_path_factory.mktemp("fp_cache"))
    try:
        if "app" in sys.modules:
            importlib.reload(sys.modules["app"])
        else:
            import app  # type: ignore  # noqa: F401
        body = sys.modules["app"].app.test_client().get("/health").get_json()
    finally:
        del os.environ["UPLOADS_DIR"]
    assert body["build"] == C.build_fingerprint()[:12]
    assert body["cache_schema"] == C.CACHE_SCHEMA_VERSION
