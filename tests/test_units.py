"""Tests that need no savegame, no built tools and no game install. These are the ones CI runs."""
import os
import subprocess
import sys
from pathlib import Path

from anno_report import paths


def test_pairs_i32_struct_of_arrays():
    from anno_report.hexutil import pairs_i32
    # one entry: plain (guid, amount)
    assert pairs_i32("61690F0094230000") == [(1010017, 9108)]
    # two entries: keys first, then values (treasury 17,754)
    assert pairs_i32("61690F00AC0000005A450000F6FFFFFF") == [(1010017, 17754), (172, -10)]


def test_hexf_is_never_decimal():
    from anno_report.hexutil import hexf
    assert hexf("00008040") == 4.0      # all digits, still hex
    assert hexf("0000803F") == 1.0
    assert hexf("") is None and hexf("1.5") is None


def test_archive_skips_archived_saves():
    from anno_report.cli import archive
    assert archive(paths.ARCHIVE / "Some Profile" / "2026-01-01_0000_Autosave 1.a7s", "Some Profile") is None


def test_parse_library_folders():
    vdf = '''"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"/home/player/.local/share/Steam"
\t\t"apps"
\t\t{
\t\t\t"228980"\t\t"284763944"
\t\t}
\t}
\t"1"
\t{
\t\t"path"\t\t"/run/media/player/Games Drive/SteamLibrary"
\t\t"apps"
\t\t{
\t\t\t"916440"\t\t"104000000000"
\t\t}
\t}
\t"2"
\t{
\t\t"path"\t\t"D:\\\\SteamLibrary"
\t}
}
'''
    assert paths.parse_library_folders(vdf) == [
        Path("/home/player/.local/share/Steam"),
        Path("/run/media/player/Games Drive/SteamLibrary"),
        Path("D:\\SteamLibrary"),
    ]


def test_game_found_in_second_library(tmp_path, monkeypatch):
    main, second = tmp_path / "main", tmp_path / "second"
    (main / "steamapps/compatdata" / paths.STEAM_APPID).mkdir(parents=True)
    (second / "steamapps/common/Anno 1800/maindata").mkdir(parents=True)
    (second / "steamapps" / f"appmanifest_{paths.STEAM_APPID}.acf").write_text("")
    (main / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{main}"\n\t}}\n\t"1"\n\t{{\n\t\t"path"\t\t"{second}"\n\t}}\n}}\n')
    monkeypatch.setattr(paths, "STEAM_ROOTS", [main])
    monkeypatch.setattr(paths, "_local_config", lambda: {})
    assert paths.steam_libraries() == [main, second]
    assert paths.game_maindata() == second / "steamapps/common/Anno 1800/maindata"
    assert paths.savegame_root().is_relative_to(main)        # the prefix stayed in the main library


def test_no_steam_raises_file_not_found(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(paths, "STEAM_ROOTS", [tmp_path / "absent"])
    monkeypatch.setattr(paths, "_local_config", lambda: {})
    with pytest.raises(FileNotFoundError):
        paths.game_maindata()
    with pytest.raises(FileNotFoundError):
        paths.savegame_root()


def test_workspace_follows_env(tmp_path):
    code = "from anno_report import paths; print(paths.WORKSPACE, paths.CACHE, paths.ARCHIVE, paths.REPORTS, paths.VENDOR_BIN, sep='\\n')"
    out = subprocess.run([sys.executable, "-I", "-c", code], env={**os.environ, "ANNO_REPORT_HOME": str(tmp_path)},
                         capture_output=True, text=True, check=True).stdout.split("\n")
    assert out[0] == str(tmp_path.resolve())
    assert out[1:4] == [str(tmp_path.resolve() / d) for d in ("cache", "archive", "reports")]
    assert out[4] == str(paths.PROJECT / "vendor" / "bin")       # the tools stay with the code


def test_tiers_and_workforce_come_from_game_data():
    from anno_report.gamedata import Names
    def tier(name, product):
        return {"name": name, "template": "PopulationLevel7", "population_outputs": [{"Product": str(product), "Amount": "100"}]}
    n = Names({
        114329: tier("Africa Population 1", 114340) | {"text": "Shepherds"},
        601379: tier("Tourists", 132723),
        15000001: tier("Moderate Population 2", 1010115) | {"text": "Workers"},
        15000005: tier("Colony01 Population 1", 1010366),
        15000000: tier("Moderate Population 1", 1010052) | {"text": "Farmers"},
        118745: tier("Moderate Scholar Population", 124478),
        112642: tier("Arctic Population 1", 112653),
        999: tier("Moon Population 1", 998),                     # a region this code has never heard of
        1010052: {"name": "moderate workforce 1", "template": "Product"},
    })
    assert n.tiers == [15000000, 15000001, 118745, 601379, 15000005, 112642, 114329, 999]
    assert n.workforce_tier[114340] == 114329 and n.workforce_tier[1010052] == 15000000 and n.workforce_tier[998] == 999
    assert n(114329) == "Shepherds" and n(15000000) == "Farmers"
    assert n.tier_sorted({999, 15000000, 42}) == [15000000, 999, 42]     # an unknown guid is kept, at the end


def test_dlc_ids_reads_both_list_shapes():
    import lxml.etree as ET
    from anno_report.save import dlc_ids
    assert dlc_ids(None) == [] and dlc_ids(ET.fromstring("<ActiveDLCs />")) == []
    assert dlc_ids(ET.fromstring("<ActiveDLCs>8361000001000000</ActiveDLCs>")) == [24963, 1]
    assert dlc_ids(ET.fromstring("<ActiveDLCs><None>24963</None><None><id>7</id></None></ActiveDLCs>")) == [24963, 7]
    assert dlc_ids(ET.fromstring("<ActiveDLCs>not hex</ActiveDLCs>")) == ["not hex"]
