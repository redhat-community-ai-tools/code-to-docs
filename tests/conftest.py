"""Shared fixtures for code-to-docs tests."""

import os
import sys
import pytest

# Add src/ to the path so we can import modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Config is now lazy (no env vars read at import time).
# Tests that need a configured client should set these env vars explicitly.


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure sensitive env vars are NOT set unless a test explicitly sets them."""
    for var in [
        "GH_TOKEN",
        "MODEL_API_KEY",
        "JIRA_API_TOKEN",
        "GOOGLE_SA_KEY",
        "MODEL_API_BASE",
        "MODEL_NAME",
        "DOCS_REPO_URL",
        "DOCS_SUBFOLDER",
        "DOCS_BASE_BRANCH",
        "PR_NUMBER",
        "PR_BASE",
        "COMMENT_BODY",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def tmp_tree(tmp_path):
    """Create a temporary directory tree for path validation tests."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide")
    (docs / "ref.adoc").write_text("= Reference")
    (docs / "tutorial.rst").write_text("Tutorial\n========")
    sub = docs / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("# Nested")
    return tmp_path


@pytest.fixture
def doc_tree(tmp_path):
    """Create a generic documentation tree for doc_index tests.

    Structure:
        tmp_path/
        ├── guides/
        │   ├── operations/
        │   │   ├── health-checks.rst
        │   │   └── monitoring.rst
        │   └── configuration/
        │       └── config-ref.rst
        ├── tutorials/
        │   └── getting-started.md
        ├── _build/           (should be skipped — underscore prefix)
        │   └── output.rst
        ├── .hidden/          (should be skipped — dot prefix)
        │   └── secret.md
        ├── overview.rst      (root-level doc, no folder)
        └── README.md         (root-level doc)
    """
    # guides folder
    ops = tmp_path / "guides" / "operations"
    ops.mkdir(parents=True)
    (ops / "health-checks.rst").write_text(
        "Health Checks\n=============\n\nHealth check reference."
    )
    (ops / "monitoring.rst").write_text(
        "Monitoring\n==========\n\nHow to monitor your system."
    )
    conf = tmp_path / "guides" / "configuration"
    conf.mkdir(parents=True)
    (conf / "config-ref.rst").write_text(
        "Config Reference\n================\n\nConfiguration options."
    )

    # tutorials folder
    tutorials = tmp_path / "tutorials"
    tutorials.mkdir()
    (tutorials / "getting-started.md").write_text("# Getting Started\n\nA quick start guide.")

    # _build folder (should be skipped)
    build = tmp_path / "_build"
    build.mkdir()
    (build / "output.rst").write_text("Build output")

    # .hidden folder (should be skipped)
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("Secret")

    # root-level docs (no folder)
    (tmp_path / "overview.rst").write_text("Overview\n========\n\nProject overview.")
    (tmp_path / "README.md").write_text("# README")

    return tmp_path
