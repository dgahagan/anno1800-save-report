"""Render a Save into reports/latest.md, reports/latest.json and reports/islands.md."""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path

from .diff import _num
from .gamedata import MONEY_GUID, Names
from .save import HUMAN, Island, Save, StockTrend

# Infolayer icons that are status, not trouble. Everything else on a building is reported as a warning.
BENIGN_ICONS = {"Awaiting promotion", "Can be downgraded", "Ready to upgrade", "For Sale", "Quest available",
                "Under Construction", "Production is paused"}
# Where ships leave a session, as a function of the map size. Only the New World -> Old World point is known
# (the corner near (1856, 0), see docs/save-format.md).
TRANSFER_POINT = {180025: lambda size: (size[0], 0)}
PASSIVE_TYPE = {2: "buy"}   # OfferMap Type; 2 = standing buy order filled by visiting traders, others unseen
RUN_OUT_MINUTES = 30
RECENT_ATTACK_MS = 15 * 60000
ROUTE_WINDOW_MS = 60 * 60000


def _ms(ms: int | None) -> str:
    if not ms:
        return "?"
    h, m = divmod(ms // 60000, 60)
    return f"{h}h{m:02d}m"


def _pct(x: float | None) -> str:
    return "?" if x is None else f"{round(x * 100)}%"


def _tier_line(n: Names, pop: dict[int, int]) -> str:
    parts = [f"{n(t)} {pop[t]:,}" for t in n.tier_sorted(pop) if pop.get(t)]
    return ", ".join(parts) if parts else "none"


def _exit_distance(island: Island, size: tuple[int, int] | None) -> float | None:
    """Distance from the island centre to the session's transfer point, where it is known."""
    tp = TRANSFER_POINT.get(island.session_guid)
    if not island.center or not size or tp is None:
        return None
    return round(math.dist(island.center, tp(size)))


def _pirate_distance(save: Save, island: Island) -> float | None:
    ds = [_dist(island, i) for s in save.sessions if s.guid == island.session_guid for i in s.islands
          if i is not island and i.owner in save.participants and save.participants[i.owner].kind == "pirate"]
    ds = [d for d in ds if d is not None]
    return min(ds) if ds else None


def island_warnings(save: Save, isl: Island) -> collections.Counter:
    """(warning text, building name) -> count, from the icons the game shows over buildings."""
    c: collections.Counter = collections.Counter()
    for b in isl.buildings:
        if b.template == "Slot" or b.blueprint:
            continue
        for g in b.icons:
            name = save.n(g)
            if name in BENIGN_ICONS or name.startswith("Pickup") or name.startswith("[") or name == str(g):
                continue
            c[(name, b.name)] += 1
    return c


def _tier_needs(save: Save, tier: int) -> set[int]:
    """Real needs of a tier from assets.xml. Bonus needs (IsBonusNeed: Flour, Sugar, Soap for Farmers ...) are left out."""
    return {int(x["Product"]) for x in (save.n.field(tier, "needs") or []) if x.get("Product") and x.get("IsBonusNeed") != "1"}


def need_status(save: Save, isl: Island) -> tuple[dict[int, list[int]], list[tuple[int, int, int]]]:
    """Sort the needs that have no consumption state on houses into locked and collapsed.

    locked: tier -> products the island has not unlocked yet (NeedsUnlockMap flag 0).
    collapsed: (tier, product, houses) for real needs of the tier that are unlocked but have no state:
    the good ran dry or the player switched the need off. Products that are not a need of the tier in
    assets.xml (Flour, Sugar ... are listed on every house) are ignored.
    """
    houses: collections.Counter = collections.Counter()
    for b in isl.residences():
        for p in b.needs_nostate:
            houses[(b.tier, p)] += 1
    locked: dict[int, list[int]] = collections.defaultdict(list)
    collapsed = []
    for (tier, p), k in sorted(houses.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        if tier is None or p not in _tier_needs(save, tier):
            continue
        if isl.need_unlocked.get(tier, {}).get(p) is False:
            locked[tier].append(p)
        elif isl.need_unlocked.get(tier, {}).get(p):
            collapsed.append((tier, p, k))
    return dict(locked), collapsed


def stock_running_out(isl: Island) -> list[tuple[int, StockTrend, float]]:
    """(product, trend, minutes left) for goods that empty within RUN_OUT_MINUTES at the recent rate.

    The trend window is up to 20 min; it is shorter right after the game was loaded."""
    out = []
    for p, t in isl.stock.items():
        if t.amount > 0 and t.delta <= -10 and t.minutes > 0:
            left = t.amount / (-t.delta / t.minutes)
            if left < RUN_OUT_MINUTES:
                out.append((p, t, left))
    return sorted(out, key=lambda x: x[2])


def _dist(a: Island, b: Island) -> float | None:
    if not a.center or not b.center:
        return None
    return round(math.dist(a.center, b.center))


def problems(save: Save) -> list[str]:
    out = []
    if save.money is not None and save.money < 5000:
        out.append(f"Treasury is low: {save.money:,} coins.")
    total_balance = save.company_finances()["balance"]
    if total_balance < 0:
        out.append(f"Estimated balance is negative ({total_balance:+,}/min): upkeep exceeds tax income.")
    for isl in save.human_islands():
        wf = save.workforce(isl)
        for tier, w in wf.items():
            if w["demand"] > w["available"]:
                out.append(f"{isl.name}: {save.n(tier)} workforce short by {w['demand'] - w['available']} ({w['available']} available, {w['demand']} needed).")
        low = [b for b in isl.factories() if (b.productivity or 0) < 0.5 and not b.paused]
        if low:
            c = collections.Counter(b.name for b in low)
            out.append(f"{isl.name}: low productivity at " + ", ".join(f"{n} x{k}" if k > 1 else n for n, k in c.most_common(6)) + ".")
        for inc in isl.incidents:
            out.append(f"{isl.name}: {inc['type']} in progress, {inc['buildings']} building(s) affected (started at a {inc['started_at']}).")
        warn = island_warnings(save, isl)
        if warn:
            out.append(f"{isl.name}: game warnings: " + "; ".join(f"{w} at {bn}" + (f" x{k}" if k > 1 else "") for (w, bn), k in warn.most_common(10)) + ".")
        for tier, p, k in need_status(save, isl)[1]:
            out.append(f"{isl.name}: {save.n(tier)} get no {save.n(p)} ({k} houses; the need is unlocked, so it ran dry or is switched off).")
        for p, t, left in stock_running_out(isl):
            out.append(f"{isl.name}: {save.n(p)} stock fell {t.points[0]} → {t.amount} in {t.minutes:.0f} min; empty in about {left:.0f} min at this rate.")
        needs = island_needs(save, isl)
        bad = [(save.n(p), s) for p, s in needs.items() if s < 0.6]
        if bad:
            out.append(f"{isl.name}: poorly supplied needs: " + ", ".join(f"{n} {_pct(s)}" for n, s in sorted(bad, key=lambda x: x[1])[:6]) + ".")
        h = isl.happiness
        if h.get("avgHappiness") is not None and h["avgHappiness"] < 30:
            out.append(f"{isl.name}: average happiness is low ({h['avgHappiness']:.0f}).")
    for pid, rel in save.relations.items():
        p = save.participants.get(pid)
        owns = any(i.owner == pid for s in save.sessions for i in s.islands)
        if p and (p.kind == "ai" or (p.kind == "third-party" and owns)) and 0 in rel.states:
            out.append(f"At war with {p.name}.")  # war with pirates is normal and not flagged
    now = save.game_time_ms or 0
    for sh in save.ships:
        if sh.owner == HUMAN and sh.last_damage_ms is not None and now - sh.last_damage_ms < RECENT_ATTACK_MS:
            who = save.participants.get(sh.last_damage_owner)
            out.append(f"Ship {sh.name} ({sh.kind}) was attacked at {_ms(sh.last_damage_ms)}" + (f" by {who.name}" if who else "") + f"{_near(save, sh)}.")
    return out


def _near(save: Save, sh) -> str:
    near = save.nearest_island(sh.session_guid, sh.pos)
    return f" near {near[0].name}" if near else ""


def island_needs(save: Save, isl: Island) -> dict[int, float]:
    """Residents-weighted average saturation per need product."""
    tot: collections.Counter = collections.Counter()
    wsum: collections.Counter = collections.Counter()
    for b in isl.residences():
        w = b.residents or 0
        for p, s in b.needs.items():
            tot[p] += s * w
            wsum[p] += w
    return {p: tot[p] / wsum[p] for p in tot if wsum[p]}


def dlc_names(save: Save) -> list[str]:
    """Names of the save's active DLC. An id the game data does not name as a DLC is shown as `id N`."""
    return [save.n(d) if save.n.template(d) == "UplayProduct" else f"id {d}" for d in save.active_dlcs]


def render_markdown(save: Save, trend_rows: list[dict] | None = None) -> str:
    n = save.n
    L = []
    L.append(f"# Anno 1800 state report — {save.save_name}")
    L.append(f"Saved {save.saved_at:%Y-%m-%d %H:%M} · game time {_ms(save.game_time_ms)} · build {save.changelist} · decode {save.dec.decode_seconds:.0f}s · sha {save.dec.sha1[:8]}")
    L.append("")
    human = save.participants.get(HUMAN)
    L.append(f"**Company:** {human.name if human else '?'} · **Treasury:** {_num(save.money)} coins · **Population:** {_num(save.total_population)}")
    L.append(f"**DLC active in this save:** {', '.join(dlc_names(save)) or 'none'}")
    pop_all: collections.Counter = collections.Counter()
    for i in save.human_islands():
        pop_all.update(i.population())
    L.append(f"**Population by tier:** {_tier_line(n, pop_all)}")
    cf = save.company_finances()
    L.append(f"**Estimated finances (per minute):** tax income {cf['tax_income']:,} · building upkeep {cf['upkeep']:,} · ship upkeep {cf['ship_upkeep']:,} · balance {cf['balance']:+,}  _(upper bound; the game's finance screen has shown less)_")
    L.append("")
    probs = problems(save)
    L.append("## Problems first")
    L.extend(f"- {p}" for p in probs) if probs else L.append("- Nothing flagged.")
    L.append("")
    if trend_rows:
        L.append("## Trend (older → newer)")
        cols = n.tier_sorted({t for r in trend_rows for t, v in r["tiers"].items() if v})    # every tier any save has
        L.append("| saved | game time | treasury | population | " + " | ".join(n(t) for t in cols) + " |")
        L.append("|---|---|---|---|" + "---|" * len(cols))
        for r in trend_rows:
            L.append(f"| {r['saved']} | {r['game_time']} | {r['money']:,} | {r['population']:,} | " + " | ".join(f"{r['tiers'].get(t, 0):,}" for t in cols) + " |")
        L.append("")
    L.append("## Your islands")
    for isl in save.human_islands():
        sess = next(s for s in save.sessions if s.guid == isl.session_guid)
        f = save.finances(isl)
        L.append(f"### {isl.name} ({sess.name})")
        L.append(f"- Population: {_tier_line(n, isl.population())}")
        L.append(f"- Finances: tax {f['tax_income']:,} · upkeep {f['upkeep']:,} · balance {f['balance']:+,}")
        wf = save.workforce(isl)
        if wf:
            L.append("- Workforce: " + ", ".join(f"{n(t)} {w['available']}/{w['demand']} needed" for t, w in wf.items()))
        h = isl.happiness
        if h:
            L.append(f"- Happiness {h.get('avgHappiness') or 0:.0f} · attractiveness {h.get('attractiveness')} · pollution {h.get('pollution') or 0:.1f}")
        L.append(f"- Fertilities: {', '.join(n(x).replace(' Fertility', '') for x in isl.fertilities) or 'none'}")
        L.append(f"- Deposits: {', '.join(f'{n(g)} x{c}' for g, c in isl.slots.most_common()) or 'none'}")
        needs = island_needs(save, isl)
        if needs:
            L.append("- Needs (supply): " + ", ".join(f"{n(p)} {_pct(s)}" for p, s in sorted(needs.items(), key=lambda x: x[1])))
        locked, collapsed = need_status(save, isl)
        if locked:
            L.append("- Needs not unlocked yet: " + "; ".join(f"{n(t)}: {', '.join(n(p) for p in ps)}" for t, ps in locked.items()))
        if collapsed:
            L.append("- Needs unlocked but not consumed (ran dry or switched off): " + ", ".join(f"{n(t)} {n(p)}" for t, p, _ in collapsed))
        fac = collections.defaultdict(list)
        for b in isl.factories():
            fac[b.name].append(b)
        if fac:
            rows = []
            for name, bs in sorted(fac.items()):
                prod = sum(b.productivity or 0 for b in bs) / len(bs)
                paused = sum(b.paused for b in bs)
                low = sum(1 for b in bs if (b.productivity or 0) < 0.5 and not b.paused)
                notes = ([f"{paused} paused"] if paused else []) + ([f"{low} under 50%"] if low and len(bs) > 1 else [])
                rows.append(f"{name} x{len(bs)} @{_pct(prod)}" + (f" ({', '.join(notes)})" if notes else ""))
            L.append("- Production: " + "; ".join(rows))
        bp = collections.Counter(b.name for b in isl.blueprints())
        if bp:
            L.append("- Blueprints (placed, not built): " + ", ".join(f"{k} x{v}" if v > 1 else k for k, v in bp.most_common()))
        warn = island_warnings(save, isl)
        if warn:
            L.append("- Game warnings: " + "; ".join(f"{w} at {bn}" + (f" x{k}" if k > 1 else "") for (w, bn), k in warn.most_common()))
        for b in isl.buildings:
            if b.queue:
                L.append(f"- {b.name} is building: " + ", ".join(f"{n(g)} ({_ms(int(rem))} left)" if rem >= 60000 else f"{n(g)} (under 1 min left)" for g, rem, _ in b.queue))
        if isl.stock:
            window = max(t.minutes for t in isl.stock.values())
            cap = isl.storage_capacity
            full = [n(p) for p, t in sorted(isl.stock.items(), key=lambda kv: n(kv[0])) if cap and t.amount >= cap]
            if cap:
                L.append(f"- Storage: {cap} per good" + (f"; full: {', '.join(full)}" if full else "; nothing is full"))
            L.append(f"- Stock now, as `stock (change over the last {window:.0f} min)`: " + ", ".join(
                f"{n(p)} {t.amount}" + (f" ({t.delta:+})" if t.delta else "") for p, t in sorted(isl.stock.items(), key=lambda kv: n(kv[0])) if t.amount or t.delta))
        snap = save.snapshots.get(isl.area_id)
        if snap:
            flows = [p for p in sorted(set(snap.generation) | set(snap.consumption), key=lambda p: n(p)) if snap.generation.get(p) or snap.consumption.get(p)]
            L.append(f"- Statistics snapshot at {_ms(snap.time_ms)} (+produced/-consumed per interval): " + ", ".join(
                f"{n(p)} +{snap.generation.get(p) or 0}/-{snap.consumption.get(p) or 0}" for p in flows))
        pt = isl.passive_trade
        if pt.get("offers") or pt.get("min_stock"):
            bits = []
            if pt["offers"]:
                bits.append("standing orders: " + ", ".join(f"{PASSIVE_TYPE.get(o['type'], 'type ' + str(o['type']))} {n(o['good'])} up to {o['target']}" for o in pt["offers"]))
            if pt["min_stock"]:
                bits.append("minimum stock kept back from traders and routes: " + ", ".join(f"{n(g)} {a}" for g, a in pt["min_stock"].items()))
            L.append("- Harbour trade: " + "; ".join(bits))
        if pt.get("purchases"):
            last = pt["purchases"][-1]
            who = save.participants.get(last["trader"])
            L.append(f"- Trader ledger: {len(pt['purchases'])} deals, {sum(x['coins'] for x in pt['purchases']):+,} coins in total; last at {_ms(last['time_ms'])}"
                     + (f" with {who.name}" if who else "") + ": " + ", ".join(f"{n(g)} {a:+}" for g, a in last["goods"]) + f" for {last['coins']:+,} coins")
        if isl.incident_chance:
            L.append("- Incident risk (type names inferred): " + ", ".join(f"{k} {v:.1f}" for k, v in isl.incident_chance.items())
                     + ("; working conditions " + ", ".join(f"{n(t)} {v:+.1f}" for t, v in isl.working_conditions.items() if v) if any(isl.working_conditions.values()) else "")
                     + ("; festival active" if isl.festival_active else ""))
        L.append("")
    L.append("## Trade routes")
    mine = [r for r in save.routes if r["owner"] == HUMAN]
    if not mine:
        L.append("- none")
    for r in mine:
        st = " → ".join((save.island_by_area(s["area_id"]).name if save.island_by_area(s["area_id"]) else str(s["area_id"])) +
                        (" [" + ", ".join(f"{'load' if load else 'unload'} {n(g)}{'' if a is None else ' ' + str(a)}" for g, a, load in s["goods"]) + "]" if s["goods"] else "")
                        for s in r["stations"])
        ships = ", ".join(r["ship_names"]) or "no ship"
        L.append(f"- {r['name'] or 'route ' + str(r['id'])} ({ships}): {st or 'no stations'}")
        moved = route_ledger(save, r["id"])
        if moved:
            L.append(f"  - Harbour ledger, last {ROUTE_WINDOW_MS // 60000} min: " + "; ".join(
                f"{'unloaded' if unloaded else 'loaded'} {n(g)} {amt} at {isl_name} in {stops} stop(s)"
                for (isl_name, g, unloaded), (amt, stops) in moved.items()))
        elif r["stations"] and r["ships"]:
            L.append(f"  - No ledger entries in the last {ROUTE_WINDOW_MS // 60000} min.")
    L.append("")
    L.append("## Ships")
    route_name = {r["id"]: r["name"] for r in mine}
    own = [sh for sh in save.ships if sh.owner == HUMAN]
    if own:
        L.append("| ship | type | upkeep | session | near | route | cargo | last attacked |")
        L.append("|---|---|---|---|---|---|---|---|")
        for sh in sorted(own, key=lambda x: (x.session_guid, x.name)):
            att = "–"
            if sh.last_damage_ms is not None:
                who = save.participants.get(sh.last_damage_owner)
                att = _ms(sh.last_damage_ms) + (f" by {who.name}" if who else "")
            cargo = ", ".join(f"{n(g)} {a}" for g, a in sh.cargo) or "empty"
            L.append(f"| {sh.name} | {sh.kind} | {sh.upkeep} | {n(sh.session_guid)} | {_near(save, sh).replace(' near ', '') or '?'} | "
                     f"{route_name.get(sh.route_id, '–') if sh.route_id is not None else '–'} | {cargo} | {att} |")
    else:
        L.append("- none in a session")
    L.append("")
    L.append("Ships crossing between sessions are not listed. Other fleets: " + (other_fleets(save) or "none seen") + ".")
    L.append("")
    if save.expeditions:
        L.append("## Expeditions on offer")
        for e in save.expeditions:
            L.append(f"- {e['name']}: threats {', '.join(e['threats']) or '?'}" + (" (ship assigned)" if e["ship"] else ""))
        L.append("")
    L.append("## Diplomacy")
    L.append("Reputation is shown on a 0–100 scale (50 = neutral). Only AIs and characters that own an island are listed; the rest are in the JSON.")
    for pid, p in sorted(save.participants.items(), key=lambda kv: (kv[1].kind != "ai", kv[1].name)):
        owned = [i.name for s in save.sessions for i in s.islands if i.owner == pid]
        if p.kind not in ("ai", "third-party", "pirate") or (p.kind != "ai" and not owned):
            continue
        rel = save.relations.get(pid)
        rep = f"{rel.reputation / 1000:.0f}" if rel and rel.reputation is not None else "?"
        L.append(f"- {p.name} ({p.kind}{', level ' + str(p.level) if p.level else ''}): {rel.label() if rel else 'no contact'}; reputation {rep}" + (f"; islands: {', '.join(owned)}" if owned else ""))
    L.append("")
    L.append("See `reports/islands.md` for every island (including unclaimed ones) and `reports/latest.json` for raw numbers.")
    return "\n".join(L)


def route_ledger(save: Save, route_id: int) -> dict[tuple[str, int, bool], tuple[int, int]]:
    """(island, good, unloaded?) -> (amount, stops) for one route over the last ROUTE_WINDOW_MS, from the harbour ledgers.

    The ledger is incomplete: in one checked save third-party harbours recorded nothing, and one island had no unload
    entries for the cross-session rum route although its rum stock rose. A missing unload is not proof of no delivery."""
    since = (save.game_time_ms or 0) - ROUTE_WINDOW_MS
    out: dict[tuple[str, int], tuple[int, int]] = {}
    for s in save.sessions:
        for isl in s.islands:
            for e in isl.passive_trade.get("route_entries", []):
                if e["route_id"] != route_id or e["time_ms"] < since:
                    continue
                for g, a in e["goods"]:
                    if g and a:
                        amt, stops = out.get((isl.name, g, a > 0), (0, 0))
                        out[(isl.name, g, a > 0)] = (amt + abs(a), stops + 1)
    return out


def other_fleets(save: Save) -> str:
    fleets: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for sh in save.ships:
        p = save.participants.get(sh.owner)
        if sh.owner != HUMAN and p and p.kind in ("ai", "pirate"):
            fleets[sh.owner][sh.kind] += 1
    return "; ".join(f"{save.participants[o].name} " + ", ".join(f"{k} x{v}" for k, v in c.most_common())
                     for o, c in sorted(fleets.items(), key=lambda kv: save.participants[kv[0]].name))


def render_islands(save: Save) -> str:
    n = save.n
    L = [f"# Islands — {save.save_name} (saved {save.saved_at:%Y-%m-%d %H:%M})", ""]
    L.append("Distances are in map tiles. `exit` = distance from the island centre to the point where ships leave the session (`?` where the exit point of that session is not known; today only the New World's is). `pirate` = distance to the nearest pirate harbour. Fertility and deposit data comes straight from the save, so it is valid for unclaimed islands too.")
    for s in save.sessions:
        L.append("")
        L.append(f"## {s.name} (map {s.map_size[0]}x{s.map_size[1]})" if s.map_size else f"## {s.name}")
        L.append("")
        L.append("| island | owner | exit | pirate | fertilities | deposits | objects |")
        L.append("|---|---|---|---|---|---|---|")
        rows = sorted(s.islands, key=lambda i: (i.owner is not None, i.owner if i.owner is not None else -1, i.name))
        for isl in rows:
            harbour = isl.owner in save.participants and save.participants[isl.owner].kind in ("pirate", "third-party")
            if not isl.fertilities and not isl.slots and isl.owner != HUMAN and not harbour:
                continue  # quest islets
            own = "**you**" if isl.owner == HUMAN else (save.participants[isl.owner].name if isl.owner in save.participants else ("unclaimed" if isl.owner is None else f"pid {isl.owner}"))
            fert = ", ".join(n(x).replace(" Fertility", "") for x in isl.fertilities)
            dep = ", ".join(f"{n(g).replace(' Slot', '').replace(' Mine', '').replace(' Deposit', '')} x{c}" for g, c in isl.slots.most_common())
            nb = sum(1 for b in isl.buildings if b.template != "Slot")
            ex, pir = _exit_distance(isl, s.map_size), _pirate_distance(save, isl)
            if harbour:
                own += f" ({save.participants[isl.owner].kind})"
            L.append(f"| {isl.name} | {own} | {'?' if ex is None else ex} | {'–' if pir is None else pir} | {fert} | {dep} | {nb} |")
        # pairwise distances between unclaimed + own islands in sessions where the human owns nothing or few
        cand = [i for i in s.islands if (i.fertilities or i.slots)]
        if 2 <= len(cand) <= 16:
            L.append("")
            L.append("Island-to-island distances:")
            L.append("")
            short = {i.area_id: i.name.split()[0][:10] for i in cand}
            L.append("| | " + " | ".join(short[i.area_id] for i in cand) + " |")
            L.append("|---|" + "---|" * len(cand))
            for a in cand:
                L.append(f"| {a.name} | " + " | ".join("–" if a is b else str(_dist(a, b)) for b in cand) + " |")
    return "\n".join(L) + "\n"


def to_json(save: Save) -> dict:
    n = save.n
    d = {
        "save": {"name": save.save_name, "saved_at": save.saved_at.isoformat() if save.saved_at else None,
                 "game_time_ms": save.game_time_ms, "build": save.changelist, "sha1": save.dec.sha1, "path": str(save.dec.save)},
        "active_dlcs": dlc_names(save), "active_dlcs_raw": save.active_dlcs,
        "money": save.money, "total_population": save.total_population, "finances": save.company_finances(),
        "max_population": {n(t): v for t, v in save.max_population.items()},
        "participants": {pid: {"name": p.name, "kind": p.kind, "level": p.level,
                               "relation": save.relations[pid].label() if pid in save.relations else None,
                               "reputation": save.relations[pid].reputation if pid in save.relations else None}
                         for pid, p in save.participants.items() if p.kind in ("human", "ai", "third-party", "pirate")},
        "sessions": [],
        "routes": save.routes,
        "ships": [{"name": sh.name, "type": sh.kind, "owner": sh.owner, "session": n(sh.session_guid), "pos": sh.pos,
                   "near": _near(save, sh).replace(" near ", "") or None, "route_id": sh.route_id,
                   "cargo": {n(g): a for g, a in sh.cargo} if len({g for g, _ in sh.cargo}) == len(sh.cargo) else [[n(g), a] for g, a in sh.cargo],
                   "upkeep": sh.upkeep, "last_damage_ms": sh.last_damage_ms, "last_damage_owner": sh.last_damage_owner}
                  for sh in save.ships if sh.owner == HUMAN],
        "other_fleets": other_fleets(save),
        "expeditions": save.expeditions,
        # raw values; meanings not decoded (see CLAUDE.md known unknowns)
        "influence_used_raw": save.influence_used, "difficulty_raw": save.difficulty, "scores_raw": save.scores,
    }
    for s in save.sessions:
        sd = {"guid": s.guid, "name": s.name, "map_size": s.map_size, "islands": []}
        for isl in s.islands:
            if not isl.fertilities and not isl.slots and isl.owner != HUMAN:
                continue
            idd = {"area_id": isl.area_id, "name": isl.name, "owner": isl.owner, "center": isl.center,
                   "exit_distance": _exit_distance(isl, s.map_size), "pirate_distance": _pirate_distance(save, isl),
                   "fertilities": [n(x) for x in isl.fertilities],
                   "deposits": {n(g): c for g, c in isl.slots.items()},
                   "population": {n(t): v for t, v in isl.population().items()},
                   "happiness": isl.happiness}
            if isl.owner == HUMAN:
                idd["finances"] = save.finances(isl)
                idd["storage_capacity"] = isl.storage_capacity
                idd["incidents"] = isl.incidents
                idd["route_entries"] = [{**e, "goods": [[n(g), a] for g, a in e["goods"]]} for e in isl.passive_trade.get("route_entries", [])]
                idd["workforce"] = {n(t): w for t, w in save.workforce(isl).items()}
                idd["needs"] = {n(p): round(v, 3) for p, v in island_needs(save, isl).items()}
                bc = collections.Counter(b.name for b in isl.buildings if b.template != "Slot" and not b.blueprint)
                idd["buildings"] = dict(bc.most_common())
                idd["blueprints"] = dict(collections.Counter(b.name for b in isl.blueprints()))
                idd["factories"] = [{"name": b.name, "productivity": b.productivity, "paused": b.paused,
                                     "outputs": [n(x) for x in b.outputs]} for b in isl.factories()]
                idd["stock"] = {n(p): {"amount": t.amount, "delta": t.delta, "minutes": t.minutes, "estimation": t.estimation}
                                for p, t in isl.stock.items()}
                idd["warnings"] = [{"warning": w, "building": bn, "count": k} for (w, bn), k in island_warnings(save, isl).most_common()]
                locked, collapsed = need_status(save, isl)
                idd["needs_locked"] = {n(t): [n(p) for p in ps] for t, ps in locked.items()}
                idd["needs_collapsed"] = [{"tier": n(t), "need": n(p), "houses": k} for t, p, k in collapsed]
                idd["incident_chance"] = isl.incident_chance
                idd["working_conditions"] = {n(t): v for t, v in isl.working_conditions.items()}
                idd["festival_active"] = isl.festival_active
                pt = isl.passive_trade
                idd["passive_trade"] = {"offers": [{**o, "good": n(o["good"])} for o in pt.get("offers", [])],
                                        "min_stock": {n(g): a for g, a in pt.get("min_stock", {}).items()},
                                        "purchases": [{**x, "goods": [[n(g), a] for g, a in x["goods"]]} for x in pt.get("purchases", [])]}
                idd["shipyard_queue"] = [{"ship": n(g), "remaining_ms": rem, "total_ms": tot} for b in isl.buildings for g, rem, tot in b.queue]
                idd["statistics_history"] = [{"time_ms": h.time_ms, "generation": {n(p): v for p, v in h.generation.items() if v},
                                              "consumption": {n(p): v for p, v in h.consumption.items() if v}}
                                             for h in save.snapshot_history(isl.area_id)]
                snap = save.snapshots.get(isl.area_id)
                if snap:
                    idd["statistics"] = {"time_ms": snap.time_ms,
                                         "products": {n(p): {"stock": snap.amount.get(p), "generation": snap.generation.get(p), "consumption": snap.consumption.get(p)}
                                                      for p in set(snap.amount) | set(snap.generation) | set(snap.consumption)}}
            sd["islands"].append(idd)
        d["sessions"].append(sd)
    return d


def write_reports(save: Save, out: Path, trend_rows=None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    md, isl, js = out / "latest.md", out / "islands.md", out / "latest.json"
    md.write_text(render_markdown(save, trend_rows) + "\n")
    isl.write_text(render_islands(save))
    js.write_text(json.dumps(to_json(save), indent=1, ensure_ascii=False, default=str))
    return [md, isl, js]
