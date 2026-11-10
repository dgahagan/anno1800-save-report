"""Savegame fixtures.

Savegames hold the player's account id, so none ship with the code. The table of saves to test is
`tests/fixtures.local.toml` in the workspace (or the file named by $ANNO_TEST_FIXTURES); copy
`tests/fixtures.example.toml` to start one. Paths in it are relative to $ANNO_TEST_SAVES, which defaults
to the workspace `archive/`. The workspace is the repo unless $ANNO_REPORT_HOME names another folder.

Tests that need a save skip when the table, the save, the built tools or the game install is missing.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from anno_report import paths

TABLE = Path(os.environ.get("ANNO_TEST_FIXTURES") or paths.WORKSPACE / "tests" / "fixtures.local.toml")
SAVES_DIR = Path(os.environ.get("ANNO_TEST_SAVES") or paths.ARCHIVE).expanduser()


def _entries() -> list[dict]:
    if not TABLE.is_file():
        return []
    with TABLE.open("rb") as fh:
        return tomllib.load(fh).get("save", [])


ENTRIES = _entries()
_loaded: dict[str, object] = {}


def _load(entry: dict):
    from anno_report.extract import decode
    from anno_report.gamedata import Names
    from anno_report.save import Save

    path = Path(entry["path"]).expanduser()
    path = path if path.is_absolute() else SAVES_DIR / path
    if not path.is_file():
        pytest.skip(f"fixture save missing: {path}")
    if not paths.rda_console().exists() or not paths.filedb_reader().exists():
        pytest.skip("vendor tools not built; run scripts/setup_tools.sh")
    try:
        names = Names()
    except FileNotFoundError as exc:
        pytest.skip(f"game data not found: {exc}")
    return Save(decode(path), names)


@pytest.fixture(scope="session", params=ENTRIES, ids=[Path(e["path"]).stem for e in ENTRIES])
def fixture_entry(request) -> dict:
    return request.param


@pytest.fixture(scope="session")
def save(fixture_entry):
    """One decoded save per table entry. Every test that takes `save` runs once per entry."""
    key = fixture_entry["path"]
    if key not in _loaded:
        _loaded[key] = _load(fixture_entry)
    return _loaded[key]


@pytest.fixture
def pins(fixture_entry):
    """Pinned values for the current save. `pins("money")` skips the test when the table has no such pin."""
    def get(key: str):
        if key not in fixture_entry:
            pytest.skip(f"no '{key}' pin for {fixture_entry['path']}")
        return fixture_entry[key]
    return get


@pytest.fixture
def expects(fixture_entry):
    """`expects("routes", found)` fails on an empty result when the table says the save has routes, else skips."""
    def check(feature: str, found) -> None:
        if found:
            return
        if feature in fixture_entry.get("has", []):
            pytest.fail(f"{fixture_entry['path']} is listed as having {feature}, none decoded")
        pytest.skip(f"save has no {feature}")
    return check
