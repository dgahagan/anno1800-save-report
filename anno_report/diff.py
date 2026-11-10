"""What changed between two saves: money, population, buildings, pauses, workforce. Written to reports/diff.md."""
from __future__ import annotations

import collections

from .save import Save


def _num(v: int | None) -> str:
    """Thousands-separated number; '?' when the save did not hold the value."""
    return "?" if v is None else f"{v:,}"


def _bcount(isl):
    return collections.Counter((b.name, "paused" if b.paused else "blueprint" if b.blueprint else "") for b in isl.buildings if b.template != "Slot")


def _need_products(isl) -> set[int]:
    """Products with a live consumption state on at least one residence (locked/empty states excluded)."""
    return {p for b in isl.residences() for p in b.needs}


def render_diff(old: Save, new: Save) -> str:
    L = [f"# Changes: {old.save_name} ({(old.game_time_ms or 0)//60000} min) → {new.save_name} ({(new.game_time_ms or 0)//60000} min)", ""]
    delta = f" ({new.money - old.money:+,})" if None not in (old.money, new.money) else ""
    L.append(f"- Treasury {_num(old.money)} → {_num(new.money)}{delta}")
    L.append(f"- Population {_num(old.total_population)} → {_num(new.total_population)}")
    old_isl = {i.area_id: i for i in old.human_islands()}
    for isl in new.human_islands():
        o = old_isl.get(isl.area_id)
        L.append(f"\n## {isl.name}")
        po, pn = (o.population() if o else collections.Counter()), isl.population()
        L.append("- Population: " + ", ".join(f"{new.n(t)} {po.get(t, 0)}→{pn.get(t, 0)}" for t in new.n.tier_sorted(set(po) | set(pn))))
        wf = new.workforce(isl)
        L.append("- Workforce spare: " + ", ".join(f"{new.n(t)} {w['available'] - w['demand']:+}" for t, w in wf.items()))
        co, cn = (_bcount(o) if o else collections.Counter()), _bcount(isl)
        changes = [(k, co.get(k, 0), cn.get(k, 0)) for k in sorted(set(co) | set(cn)) if co.get(k, 0) != cn.get(k, 0)]
        for (name, state), a, b in changes:
            L.append(f"- {name}{' (' + state + ')' if state else ''}: {a} → {b}")
        if not changes:
            L.append("- no building changes")
        # needs whose consumption state appeared or vanished (a vanished state = supply collapsed or switched off)
        no, nn = (_need_products(o) if o else set()), _need_products(isl)
        lost, gained = sorted(no - nn), sorted(nn - no)
        if lost:
            L.append("- Needs that LOST their state (supply collapsed or switched off): " + ", ".join(new.n(p) for p in lost))
        if gained:
            L.append("- Needs that gained a state: " + ", ".join(new.n(p) for p in gained))
        lo = [b for b in isl.factories() if (b.productivity or 0) < 0.5 and not b.paused]
        if lo:
            L.append("- Under 50% productivity: " + ", ".join(f"{k} x{v}" for k, v in collections.Counter(b.name for b in lo).items()))
    nb = {i.area_id for i in new.human_islands()} - set(old_isl)
    if nb:
        L.append("\n## New islands: " + ", ".join(i.name for i in new.human_islands() if i.area_id in nb))
    return "\n".join(L) + "\n"
