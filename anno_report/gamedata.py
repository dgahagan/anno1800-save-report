"""Extract assets.xml + texts_english.xml from the game archives and build a GUID table.

The highest-numbered maindata/data*.rda holds the current assets.xml (per the anno-mods
modding guide). The table is cached in cache/gamedata/guid_table.json, keyed by RDA mtime.
Assets can inherit from a BaseAssetGUID; `Names.field()` follows that chain.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import lxml.etree as ET

from . import paths

ASSETS_REL = "data/config/export/main/asset/assets.xml"
TEXTS_REL = "data/config/gui/texts_english.xml"
GAMEDATA = paths.CACHE / "gamedata"

MONEY_GUID = 1010017
# Population tiers and their workforce goods are not listed here: Names.tiers and Names.workforce_tier read them
# from the PopulationLevel7 assets, so DLC tiers (Explorers, Shepherds, Scholars, Tourists, Artista...) are included.
# Display order of the regions, by the prefix of the tier asset's internal name. Unknown regions go last.
_TIER_REGION_ORDER = ("Moderate", "Tourists", "Colony01", "Arctic", "Africa")


def latest_rda() -> Path:
    rdas = [p for p in paths.game_maindata().glob("data*.rda") if re.fullmatch(r"data\d+\.rda", p.name)]
    if not rdas:
        raise FileNotFoundError(f"no data*.rda in {paths.game_maindata()}")
    return max(rdas, key=lambda p: int(p.stem[4:]))


def extract_game_xml(force: bool = False) -> tuple[Path, Path]:
    out = GAMEDATA / "extracted"
    assets, texts = out / ASSETS_REL, out / TEXTS_REL
    if assets.exists() and texts.exists() and not force:
        return assets, texts
    out.mkdir(parents=True, exist_ok=True)
    rda = latest_rda()
    subprocess.run(
        [str(paths.rda_console()), "extract", "-f", str(rda), "-o", str(out) + "/",
         "--filter", r"(^|[\\/])data[\\/]config[\\/](export[\\/]main[\\/]asset[\\/]assets|gui[\\/]texts_english)\.xml$", "-y", "-n"],
        cwd=out, check=True, capture_output=True,
    )
    for f in out.iterdir():
        if "\\" in f.name:
            dst = out / f.name.replace("\\", "/").lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dst)
    if not (assets.exists() and texts.exists()):
        raise RuntimeError(f"extraction from {rda} did not yield {ASSETS_REL} and {TEXTS_REL}")
    return assets, texts


def _items(node, path, fields):
    """[{field: text}] for each Item under path."""
    out = []
    for it in node.findall(f"{path}/Item"):
        d = {f: it.findtext(f) for f in fields if it.findtext(f) is not None}
        if d:
            out.append(d)
    return out


def _asset_entry(asset) -> tuple[int, dict] | None:
    std = asset.find("Values/Standard")
    guid = std.findtext("GUID") if std is not None else None
    if not guid or not guid.lstrip("-").isdigit():
        return None
    v = asset.find("Values")
    e: dict = {"name": std.findtext("Name") or "", "template": asset.findtext("Template") or ""}
    base = asset.findtext("BaseAssetGUID")
    if base:
        e["base"] = int(base)
    txt = v.findtext("Text/LocaText/English/Text")
    if txt:
        e["text"] = txt
    for tag, key in (("Building/BuildingType", "building_type"), ("Building/AssociatedRegions", "regions"),
                     ("Residence7/PopulationLevel7", "population_level"), ("Residence7/ResidentMax", "resident_max"),
                     ("Upgradable/NextGUID", "upgrade_to"), ("Product/ProductCategory", "product_category"),
                     ("FactoryBase/CycleTime", "cycle_time"), ("Fertility/Fertility", "fertility_product"),
                     ("ShipMaintenance/Maintenance", "ship_maintenance")):
        val = v.findtext(tag)
        if val is not None:
            e[key] = val
    for path, key, fields in (("FactoryBase/FactoryInputs", "inputs", ("Product", "Amount")),
                              ("FactoryBase/FactoryOutputs", "outputs", ("Product", "Amount")),
                              ("Maintenance/Maintenances", "maintenance", ("Product", "Amount", "InactiveAmount")),
                              ("PopulationLevel7/PopulationOutputs", "population_outputs", ("Product", "Amount")),
                              ("PopulationLevel7/PopulationInputs", "needs",
                               ("Product", "Amount", "SupplyWeight", "HappinessValue", "MoneyValue",
                                "FullWeightPopulationCount", "NoWeightPopulationCount", "IsBonusNeed"))):
        items = _items(v, path, fields)
        if items:
            e[key] = items
    return int(guid), e


def build_guid_table(force: bool = False) -> dict[int, dict]:
    cache = GAMEDATA / "guid_table.json"
    rda = latest_rda()
    stamp = f"{rda.name}:{int(rda.stat().st_mtime)}:v4"
    if cache.exists() and not force:
        data = json.loads(cache.read_text())
        if data.get("_stamp") == stamp:
            return {int(k): v for k, v in data["guids"].items()}
    assets_xml, texts_xml = extract_game_xml()
    table: dict[int, dict] = {}
    for _, asset in ET.iterparse(str(assets_xml), tag="Asset", huge_tree=True):
        got = _asset_entry(asset)
        if got:
            table[got[0]] = got[1]
        asset.clear()
    for _, t in ET.iterparse(str(texts_xml), tag="Text", huge_tree=True):
        g, txt = t.findtext("GUID"), t.findtext("Text")
        if g and g.lstrip("-").isdigit() and txt is not None:
            table.setdefault(int(g), {"name": "", "template": ""}).setdefault("text", txt)
        t.clear()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"_stamp": stamp, "guids": table}, ensure_ascii=False))
    return table


class Names:
    """Human-readable names and inherited fields for GUIDs."""

    def __init__(self, table: dict[int, dict] | None = None):
        self.table = table if table is not None else build_guid_table()
        self.tiers, self.workforce_tier = self._population_tiers()

    def _population_tiers(self) -> tuple[list[int], dict[int, int]]:
        """(tier guids in display order, workforce product -> the tier that supplies it), from the game data."""
        def rank(guid: int):
            name = self.table[guid].get("name", "")
            region = next((i for i, r in enumerate(_TIER_REGION_ORDER) if name.startswith(r)), len(_TIER_REGION_ORDER))
            return (0 if region <= 1 else region, name, guid)      # Tourists live in the Old World, after its own tiers
        tiers = sorted((g for g, e in self.table.items() if e.get("template") == "PopulationLevel7"), key=rank)
        workforce = {int(o["Product"]): t for t in tiers for o in self.table[t].get("population_outputs", []) if o.get("Product")}
        return tiers, workforce

    def tier_sorted(self, guids) -> list[int]:
        """Tier guids in display order; guids that are not known tiers keep their place at the end."""
        guids = list(guids)
        return [t for t in self.tiers if t in guids] + sorted(g for g in guids if g not in self.tiers)

    def entry(self, guid) -> dict:
        return self.table.get(int(guid), {}) if guid is not None else {}

    def field(self, guid, key, default=None):
        """Field value, following BaseAssetGUID inheritance."""
        seen = set()
        g = int(guid) if guid is not None else None
        while g is not None and g not in seen:
            seen.add(g)
            e = self.table.get(g)
            if not e:
                break
            if key in e:
                return e[key]
            g = e.get("base")
        return default

    def __call__(self, guid) -> str:
        if guid is None:
            return "?"
        g = int(guid)
        txt = self.field(g, "text")
        if txt:
            return txt
        name = self.field(g, "name")
        if name:
            m = re.search(r"\(([^()]*)\)\s*$", name)  # "coastal_01 (Fish Coast Building)" -> inner part
            return m.group(1) if m else name
        return str(g)

    def template(self, guid) -> str:
        return self.field(guid, "template", "") or ""
