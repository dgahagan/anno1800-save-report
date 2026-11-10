"""Locate the project, vendored tools, game install and savegames on this machine.

Two roots. PROJECT holds the code and the built tools (vendor/). WORKSPACE holds one player's data:
archive/, cache/, reports/ and config.local.toml. They are the same folder unless ANNO_REPORT_HOME is set.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ["ANNO_REPORT_HOME"]).expanduser().resolve() if os.environ.get("ANNO_REPORT_HOME") else PROJECT
VENDOR_BIN = PROJECT / "vendor" / "bin"
VENDOR_SRC = PROJECT / "vendor" / "src"
CACHE = WORKSPACE / "cache"
ARCHIVE = WORKSPACE / "archive"
REPORTS = WORKSPACE / "reports"
RULES_ALL = VENDOR_SRC / "Anno1800SavegameVisualizer/tools/FileDBReader/FileFormats/a7s_all.xml"
RULES_HEX = VENDOR_SRC / "Anno1800SavegameVisualizer/tools/FileDBReader/FileFormats/a7s_hex.xml"

STEAM_APPID = "916440"
STEAM_ROOTS = [
    Path.home() / ".local/share/Steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".steam/root",
]


def _local_config() -> dict:
    cfg = WORKSPACE / "config.local.toml"
    if cfg.exists():
        with cfg.open("rb") as fh:
            return tomllib.load(fh)
    return {}


def rda_console() -> Path:
    return VENDOR_BIN / "RdaConsole" / "RdaConsole"


def filedb_reader() -> Path:
    return VENDOR_BIN / "FileDBReader" / "FileDBReader"


def steam_root() -> Path | None:
    """First Steam installation found (config `steam_root` wins)."""
    cfg = _local_config().get("steam_root")
    if cfg:
        return Path(cfg).expanduser()
    for root in STEAM_ROOTS:
        if (root / "steamapps").is_dir():
            return root
    return None


def parse_library_folders(text: str) -> list[Path]:
    """Library paths from Steam's libraryfolders.vdf, in file order."""
    return [Path(m.replace("\\\\", "\\")) for m in re.findall(r'^\s*"path"\s+"([^"]*)"', text, re.M)]


def steam_libraries() -> list[Path]:
    """Every Steam library folder: each Steam root plus the libraries its libraryfolders.vdf lists."""
    cfg = _local_config().get("steam_root")
    roots = [Path(cfg).expanduser()] if cfg else STEAM_ROOTS
    out: list[Path] = []
    for root in roots:
        if not (root / "steamapps").is_dir():
            continue
        libs = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            libs += parse_library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
        for lib in libs:
            # samefile, not resolve(): /home and /var/home can be one folder through a bind mount
            if (lib / "steamapps").is_dir() and not any(lib.samefile(o) for o in out):
                out.append(lib)
    return out


def _library_with(relative: str) -> Path | None:
    """The first library that holds steamapps/<relative>."""
    for lib in steam_libraries():
        if (lib / "steamapps" / relative).exists():
            return lib
    return None


def savegame_root() -> Path:
    """Folder that holds accounts/<account-id>/<profile>/*.a7s."""
    cfg = _local_config().get("savegame_root")
    if cfg:
        return Path(cfg).expanduser()
    # The Proton prefix is normally in the library that holds the game, but Steam can leave it in the main one.
    lib = _library_with(f"compatdata/{STEAM_APPID}") or steam_root()
    if lib is None:
        raise FileNotFoundError("Steam not found; set savegame_root in config.local.toml")
    return lib / "steamapps/compatdata" / STEAM_APPID / "pfx/drive_c/users/steamuser/Documents/Anno 1800"


def game_maindata() -> Path:
    cfg = _local_config().get("game_maindata")
    if cfg:
        return Path(cfg).expanduser()
    lib = _library_with(f"appmanifest_{STEAM_APPID}.acf") or _library_with("common/Anno 1800") or steam_root()
    if lib is None:
        raise FileNotFoundError("Steam not found; set game_maindata in config.local.toml")
    return lib / "steamapps/common/Anno 1800/maindata"


def profiles() -> dict[str, Path]:
    """Profile name -> folder, across all accounts."""
    out: dict[str, Path] = {}
    accounts = savegame_root() / "accounts"
    if not accounts.is_dir():
        return out
    for acct in accounts.iterdir():
        if not acct.is_dir():
            continue
        for prof in acct.iterdir():
            if prof.is_dir() and any(prof.glob("*.a7s")):
                out[prof.name] = prof
    return out


def default_profile() -> str | None:
    return _local_config().get("profile") or os.environ.get("ANNO_PROFILE")


def list_saves(profile: str | None = None) -> list[Path]:
    """All .a7s saves, newest first. Restricted to one profile if given."""
    profs = profiles()
    if profile:
        if profile not in profs:
            raise FileNotFoundError(f"profile {profile!r} not found; have {sorted(profs)}")
        folders = [profs[profile]]
    else:
        folders = list(profs.values())
    saves = [p for f in folders for p in f.glob("*.a7s")]
    return sorted(saves, key=lambda p: p.stat().st_mtime, reverse=True)
