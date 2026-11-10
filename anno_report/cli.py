"""anno-report: decode the newest Anno 1800 save and write reports/latest.md."""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

from . import paths
from .extract import decode
from .gamedata import Names, build_guid_table
from .report import write_reports
from .save import Save


def archive(save_path: Path, profile: str) -> Path | None:
    """Copy an unseen save into archive/<profile>/<YYYY-MM-DD_HHMM>_<name>.a7s. Returns the new path or None."""
    if paths.ARCHIVE in save_path.resolve().parents:
        return None  # already an archived copy; copying it again would double the date prefix
    folder = paths.ARCHIVE / profile
    folder.mkdir(parents=True, exist_ok=True)
    mtime = dt.datetime.fromtimestamp(save_path.stat().st_mtime)
    dst = folder / f"{mtime:%Y-%m-%d_%H%M}_{save_path.stem}.a7s"
    if dst.exists() and dst.stat().st_size == save_path.stat().st_size:
        return None
    shutil.copy2(save_path, dst)
    return dst


def trend_saves(profile: str, n: int) -> list[Path]:
    """The newest n saves from the archive and the live profile together (the game rotates autosaves away)."""
    live = paths.list_saves(profile) if profile in paths.profiles() else []
    seen, out = set(), []
    for p in sorted([*live, *(paths.ARCHIVE / profile).glob("*.a7s")], key=lambda p: p.stat().st_mtime, reverse=True):
        key = (int(p.stat().st_mtime), p.stat().st_size)   # archive copies keep the original mtime
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[:n]


def prune_cache(keep: int) -> tuple[int, int]:
    """Remove decode folders without a .done marker and all but the newest `keep` finished ones. Returns (folders, bytes)."""
    done, removed, freed = [], 0, 0
    for d in paths.CACHE.iterdir():
        if not d.is_dir() or d.name == "gamedata":
            continue
        (done if (d / ".done").exists() else []).append(d)
        if not (d / ".done").exists():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
            removed += 1
    for d in sorted(done, key=lambda d: (d / ".done").stat().st_mtime, reverse=True)[max(keep, 0):]:
        freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        shutil.rmtree(d)
        removed += 1
    return removed, freed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="anno-report")
    ap.add_argument("--save", type=Path, help="a specific .a7s file (default: newest in the profile)")
    ap.add_argument("--profile", default=paths.default_profile(), help="save profile folder name")
    ap.add_argument("--out", type=Path, default=paths.REPORTS)
    ap.add_argument("--trend", type=int, default=0, metavar="N", help="also decode the last N saves for a trend table")
    ap.add_argument("--force", action="store_true", help="ignore the decode cache")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--since", type=Path, metavar="SAVE", help="also write reports/diff.md against this save (default: the previous archived save)")
    ap.add_argument("--no-diff", action="store_true")
    ap.add_argument("--prune-cache", type=int, metavar="N", help="delete failed decodes and all but the newest N decoded saves from cache/, then exit")
    a = ap.parse_args(argv)
    if a.prune_cache is not None:
        removed, freed = prune_cache(a.prune_cache)
        print(f"removed {removed} cache folder(s), freed {freed / 1e9:.1f} GB")
        return 0

    if a.save:
        save_path = a.save
        profile = a.save.parent.name
    else:
        saves = paths.list_saves(a.profile)
        if not saves:
            print("no saves found", file=sys.stderr)
            return 1
        save_path, profile = saves[0], saves[0].parent.name
    if not a.no_archive:
        for p in ([save_path] if a.save else paths.list_saves(profile)):
            archive(p, profile)

    names = Names(build_guid_table())
    dec = decode(save_path, force=a.force)
    save = Save(dec, names)

    trend_rows = None
    if a.trend:
        rows = []
        for p in sorted(trend_saves(profile, a.trend), key=lambda p: p.stat().st_mtime):
            d = decode(p)
            s = Save(d, names) if d.sha1 != dec.sha1 else save
            pop = {}
            for i in s.human_islands():
                for t, v in i.population().items():
                    pop[t] = pop.get(t, 0) + v
            rows.append({"saved": f"{s.saved_at:%m-%d %H:%M}", "game_time": f"{(s.game_time_ms or 0) // 60000} min",
                         "money": s.money or 0, "population": s.total_population or 0, "tiers": pop})
        trend_rows = rows

    files = write_reports(save, a.out, trend_rows)
    if not a.no_diff:
        prev = a.since
        if prev is None:
            arch = sorted((paths.ARCHIVE / profile).glob("*.a7s"), key=lambda p: p.stat().st_mtime)
            older = [p for p in arch if p.stat().st_mtime < save_path.stat().st_mtime - 1]
            prev = older[-1] if older else None
        if prev is not None:
            from .diff import render_diff
            old = Save(decode(prev), names)
            (a.out / "diff.md").write_text(render_diff(old, save))
    print(f"wrote {files[0]} (save: {save_path.name}, saved {save.saved_at:%Y-%m-%d %H:%M}, decode {dec.decode_seconds:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
