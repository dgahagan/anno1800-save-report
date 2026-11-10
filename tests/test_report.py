"""Tests against decoded savegames. Which saves, and their pinned values: see conftest.py and fixtures.local.toml.

Invariant tests hold for any save. Pinned tests compare with numbers read off the game screens.
"""
from anno_report.save import HUMAN


# ---------- pinned values (skipped for a save with no such pin) ----------

def test_header_totals(save, pins):
    assert save.money == pins("money")
    assert save.total_population == pins("population")
    assert save.changelist == pins("changelist")


def test_pinned_islands(save, pins):
    mine = {i.name: i for i in save.human_islands()}
    for name, want in pins("islands").items():
        isl = mine[name]
        pop = isl.population()
        for tier, count in want.get("population", {}).items():
            assert pop[int(tier)] == count, (name, tier)
        assert {save.n(f).split(" ")[0] for f in isl.fertilities} >= set(want.get("fertilities", []))
        if "workforce_tiers" in want:
            assert set(save.workforce(isl)) == set(want["workforce_tiers"])


def test_participants(save, pins):
    ais = {p.name for p in save.participants.values() if p.kind == "ai"}
    assert ais == set(pins("ais"))
    assert save.participants[HUMAN].name == pins("company")


def test_unclaimed_islands(save, pins):
    for guid, count in pins("unclaimed_islands").items():
        session = next(s for s in save.sessions if s.guid == int(guid))
        unclaimed = [i for i in session.islands if i.owner is None and i.fertilities]
        assert len(unclaimed) == count
        assert all(i.slots for i in unclaimed)


# ---------- invariants (every save) ----------

def test_population_matches_header(save):
    total = sum(sum(i.population().values()) for i in save.human_islands())
    assert total == save.total_population


def test_money_matches_header(save):
    assert save.money == save.total_money


def test_needs_nostate_separated(save):
    # every residence keeps empty-state needs apart from live ones; the two sets never overlap
    for isl in save.human_islands():
        for b in isl.residences():
            assert not (set(b.needs) & b.needs_nostate)


def test_object_owner_ids_are_meta_participant_ids(save):
    # ParticipantID/id on objects is decimal. Every owner of an AI island's buildings must be a known participant.
    for s in save.sessions:
        for isl in s.islands:
            p = save.participants.get(isl.owner)
            if p and p.kind == "ai":
                owners = {b.owner for b in isl.buildings if b.residents is not None}
                assert owners == {isl.owner}, (isl.name, owners)


def test_ship_owners_are_known_participants(save, expects):
    assert all(sh.owner in save.participants for sh in save.ships)
    own = [sh for sh in save.ships if sh.owner == HUMAN]
    expects("ships", own)
    assert all(sh.name for sh in own)


def test_route_stations_split_load_and_unload(save, expects):
    mine = [r for r in save.routes if r["owner"] == HUMAN and len(r["stations"]) >= 2]
    expects("routes", mine)
    for r in mine:
        flags = {load for st in r["stations"] for _, _, load in st["goods"]}
        assert flags == {True, False}, r["name"]      # something is loaded and something unloaded
        assert len(r["ship_names"]) == r["ships"]


def test_live_stock(save):
    stocked = [i for i in save.human_islands() if i.stock]
    assert stocked
    for isl in stocked:
        for t in isl.stock.values():
            assert 1 <= len(t.points) <= 120 and t.amount == t.points[-1]   # 120 = 20 min; fewer right after a load
    # the newest trend point is at most one 10 s step older than the save
    newest = max(t.time_ms for isl in stocked for t in isl.stock.values())
    assert 0 <= save.game_time_ms - newest <= 10_000


def test_need_status_ignores_bonus_needs(save):
    from anno_report.report import need_status
    for isl in save.human_islands():
        locked, collapsed = need_status(save, isl)
        names = {save.n(p) for ps in locked.values() for p in ps} | {save.n(p) for _, p, _ in collapsed}
        assert not names & {"Flour", "Sugar"}


def test_reports_render(save, tmp_path):
    import json
    from anno_report.diff import render_diff
    from anno_report.report import write_reports
    md, isl, js = write_reports(save, tmp_path)
    text = md.read_text()
    for heading in ("## Problems first", "## Trade routes", "## Ships", "## Diplomacy"):
        assert heading in text
    assert "| island | owner | exit | pirate |" in isl.read_text()
    data = json.loads(js.read_text())
    mine = [i for s in data["sessions"] for i in s["islands"] if i["owner"] == HUMAN]
    assert mine and all("stock" in i and "warnings" in i and "statistics_history" in i for i in mine)
    assert "no building changes" in render_diff(save, save)


def test_company_finances_include_ship_upkeep(save):
    cf = save.company_finances()
    own = [sh for sh in save.ships if sh.owner == HUMAN]
    assert cf["ship_upkeep"] == sum(sh.upkeep for sh in own)
    assert cf["balance"] == cf["tax_income"] - cf["upkeep"] - cf["ship_upkeep"]


def test_storage_capacity_bounds_stock(save):
    for isl in save.human_islands():
        assert isl.storage_capacity > 0
        if isl.stock:
            assert max(t.amount for t in isl.stock.values()) <= isl.storage_capacity


def test_workforce_matches_verified_formula(save):
    # demand comes from workforce product guids; every tier key is a population tier, and supply is its residents
    for isl in save.human_islands():
        pop = isl.population()
        for tier, w in save.workforce(isl).items():
            assert w["available"] == pop.get(tier, 0), (isl.name, tier)
            assert w["demand"] >= 0


def test_route_ledger_signs(save, expects):
    from anno_report.report import route_ledger
    entries = [e for i in save.human_islands() for e in i.passive_trade["route_entries"]]
    expects("route_ledger", entries)
    assert all(e["route_id"] is not None for e in entries)
    mine = {r["id"] for r in save.routes if r["owner"] == HUMAN}
    assert {e["route_id"] for e in entries} <= mine
    for rid in mine:
        assert all(amt > 0 and stops > 0 for amt, stops in route_ledger(save, rid).values())
