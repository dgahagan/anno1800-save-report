"""Unpack and decode an .a7s savegame into XML, cached by content hash.

Pipeline (same as NiHoel's Anno1800SavegameVisualizer, minus the Windows bits):
  .a7s (RDA V2.2 container) --RdaConsole--> data.a7s meta.a7s header.a7s gamesetup.a7s
  each inner file --zlib--> .bin --FileDBReader + a7s_all.xml--> .xml with decoded leaves
The save is copied into cache/ first; the game folder is never touched.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import paths

INNER = ("data", "meta", "header", "gamesetup")


@dataclass
class Decoded:
    save: Path          # original path
    copy: Path          # cache copy of the .a7s
    folder: Path        # cache/<sha1>/
    sha1: str
    decode_seconds: float

    def xml(self, name: str) -> Path:
        return self.folder / f"{name}.xml"


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(args: list[str], cwd: Path) -> None:
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{args[0]} failed ({res.returncode}):\n{res.stdout}\n{res.stderr}")


def _unpack_rda(copy: Path, out: Path) -> None:
    # Trailing slash matters: RDAExplorer builds "<out>\\name" and on Linux the
    # backslash is not a separator, so without the slash the directory part is "".
    _run([str(paths.rda_console()), "extract", "-f", str(copy), "-o", str(out) + "/", "-y", "-n"], cwd=out)
    for f in out.iterdir():
        if "\\" in f.name:
            f.rename(out / f.name.replace("\\", "/").lstrip("/"))
    missing = [n for n in INNER if not (out / f"{n}.a7s").exists()]
    if missing:
        raise RuntimeError(f"RdaConsole did not produce {missing} in {out}")


def _decode_filedb(out: Path, rules: Path) -> None:
    for n in INNER:
        raw = (out / f"{n}.a7s").read_bytes()
        (out / f"{n}.bin").write_bytes(zlib.decompress(raw))
        _run([str(paths.filedb_reader()), "decompress", "-i", str(rules), "-f", f"{n}.bin", "-y"], cwd=out)
        if not (out / f"{n}.xml").exists() or (out / f"{n}.xml").stat().st_size == 0:
            raise RuntimeError(f"FileDBReader produced no XML for {n}")
        (out / f"{n}.bin").unlink()


def decode(save: Path, rules: Path | None = None, force: bool = False) -> Decoded:
    rules = rules or paths.RULES_ALL
    paths.CACHE.mkdir(exist_ok=True)
    # copy first: the game may be writing an autosave right now
    tmp = paths.CACHE / f".incoming-{os.getpid()}.a7s"
    try:
        shutil.copy2(save, tmp)
        digest = sha1_of(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    folder = paths.CACHE / digest
    copy = folder / "save.a7s"
    done = folder / ".done"
    if done.exists() and not force:
        tmp.unlink()
        return Decoded(save, copy, folder, digest, float(done.read_text() or 0))
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    tmp.rename(copy)
    t0 = time.time()
    try:
        _unpack_rda(copy, folder)
        _decode_filedb(folder, rules)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)   # do not leave a half-decoded folder (data.bin is ~75 MB)
        raise
    secs = time.time() - t0
    done.write_text(f"{secs:.1f}")
    return Decoded(save, copy, folder, digest, secs)
