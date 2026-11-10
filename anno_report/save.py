"""Object model over a decoded savegame (data.xml + header.xml)."""
from __future__ import annotations

import collections
import datetime as dt
import math
from dataclasses import dataclass, field

import lxml.etree as ET

from .extract import Decoded
from .gamedata import MONEY_GUID, Names
from .hexutil import h2f, h2i, h2i_hex, hexf, is_hex, pairs_i32, utf16

HUMAN = 0
# DiplomacyStates ids seen in saves; 0 and 1 are certain, 2 and 3 are best guesses until verified in-game
DIPLO_STATE = {0: "war", 1: "peace", 2: "trade rights", 3: "alliance?"}
# AreaIncidentManager type ids. Inferred 2026-11-10 from the per-building factors (0 has base+density, 1 has
# residenceHappiness, 6 carries an `Unlocked` flag) and the IncidentType names in assets.xml. Not verified in game.
# 0 = fire is confirmed: the buildings of a type-0 incident carry the "Fire!" icon (Autosave of 2026-11-01).
INCIDENT_TYPE = {0: "fire", 1: "riot", 2: "illness", 5: "explosion", 6: "arctic illness"}
TREND_POINT_MS = 10_000  # StorageTrends keeps one point per 10 s (measured between two saves: 36 points per 360 s)


def _kv(node) -> list[tuple]:
    """Children of a FileDB map node alternate key, value."""
    kids = list(node) if node is not None else []
    return list(zip(kids[0::2], kids[1::2]))


@dataclass
class Building:
    guid: int
    name: str
    template: str
    pos: tuple[float, float] | None
    owner: int | None                     # meta participant id; None on the human's own objects
    obj_id: int | None = None
    icons: list[int] = field(default_factory=list)               # Infolayer icon guids: the warning/status icons the game shows
    queue: list[tuple[int, float, float]] = field(default_factory=list)  # Shipyard: (ship guid, remaining ms, total ms)
    storage: int = 0                      # Warehouse/StorageCapacityRegistered: what this building adds to the island's cap per good
    residents: int | None = None          # Residence7/ResidentCount
    tier: int | None = None               # PopulationLevel7 guid
    money_output: int | None = None       # Residence7/ProductMoneyOutput
    need_saturation: float | None = None  # Residence7/AverageNeedSaturation
    productivity: float | None = None     # Factory7/ProductionState/Productivity (fallback CurrentProductivity)
    current_productivity: float | None = None
    needs: dict[int, float] = field(default_factory=dict)        # product guid -> CurrentSaturation (residences)
    needs_nostate: set[int] = field(default_factory=set)         # listed in ConsumptionStates with an empty state: locked, switched off, or supply collapsed
    paused: bool = False
    blueprint: bool = False               # placed in blueprint mode, not built (StateBits present; same rule as NiHoel)
    maintenance: dict[int, int] = field(default_factory=dict)   # product guid -> amount (coins, workforce)
    outputs: list[int] = field(default_factory=list)


@dataclass
class StockTrend:
    """Live warehouse stock of one good: AreaEconomy/StorageTrends, one point per 10 s, newest last."""
    points: list[int]
    time_ms: int            # game time of the newest point
    estimation: int | None  # the game's own trend figure; unit unverified (looks like t/min)

    @property
    def amount(self) -> int:
        return self.points[-1]

    @property
    def delta(self) -> int:
        return self.points[-1] - self.points[0]

    @property
    def minutes(self) -> float:
        return (len(self.points) - 1) * TREND_POINT_MS / 60000


@dataclass
class Ship:
    guid: int
    name: str               # player-visible ship name
    kind: str               # asset name, e.g. Schooner
    template: str           # TradeShip / WarShip
    owner: int              # meta participant id (0 = human)
    session_guid: int
    pos: tuple[float, float] | None
    meta_id: int | None     # MetaPersistent/MetaID; trade routes list their ships by this id
    route_id: int | None
    cargo: list[tuple[int, int]]          # (guid, amount); items count as 1
    last_damage_ms: int | None = None
    last_damage_owner: int | None = None
    upkeep: int = 0         # coins per minute, ShipMaintenance/Maintenance in assets.xml


@dataclass
class Island:
    session_guid: int
    area_id: int
    name: str
    owner: int | None                     # participant id, None = unclaimed
    fertilities: list[int]
    slots: collections.Counter            # slot guid -> count
    buildings: list[Building]
    center: tuple[float, float] | None
    happiness: dict
    stock: dict[int, StockTrend] = field(default_factory=dict)             # product -> live stock + 20 min history
    need_unlocked: dict[int, dict[int, bool]] = field(default_factory=dict)  # tier -> product -> unlocked on this island
    incident_chance: dict[str, float] = field(default_factory=dict)        # incident type -> AreaInfectionChance
    working_conditions: dict[int, float] = field(default_factory=dict)     # tier -> value (0 = normal)
    festival_active: bool = False
    passive_trade: dict = field(default_factory=dict)                      # offers, min_stock, purchases, route_entries
    incidents: list[dict] = field(default_factory=list)                    # active incidents: type, buildings, start building

    @property
    def storage_capacity(self) -> int:
        """Cap per good. Trading post + depots; plain warehouses register 0. Matched the highest stock on all three
        islands in one checked save (200 / 125 / 125)."""
        return sum(b.storage for b in self.buildings if not b.blueprint)

    @property
    def claimed(self) -> bool:
        return self.owner is not None

    def population(self) -> collections.Counter:
        c: collections.Counter = collections.Counter()
        for b in self.buildings:
            if b.residents is not None and b.tier is not None:
                c[b.tier] += b.residents
        return c

    def residences(self) -> list[Building]:
        return [b for b in self.buildings if b.residents is not None]

    def factories(self) -> list[Building]:
        return [b for b in self.buildings if b.productivity is not None and not b.blueprint]

    def blueprints(self) -> list[Building]:
        return [b for b in self.buildings if b.blueprint]


@dataclass
class Session:
    guid: int
    name: str
    map_size: tuple[int, int] | None
    playable: tuple[int, int, int, int] | None
    islands: list[Island]
    total_time_ms: int | None
    ships: list[Ship] = field(default_factory=list)


@dataclass
class Relation:
    other: int
    reputation: int | None
    states: list[int]

    def label(self) -> str:
        return ", ".join(DIPLO_STATE.get(s, f"state {s}") for s in self.states) or "none"


@dataclass
class Participant:
    pid: int
    name: str
    kind: str          # human | ai | third-party | other
    level: int | None
    guid: int


@dataclass
class Snapshot:
    area_id: int
    time_ms: int
    population: dict[int, int]
    amount: dict[int, int]
    generation: dict[int, int]
    consumption: dict[int, int]


def dlc_ids(node) -> list[int | str]:
    """Ids in a save's ActiveDLCs node. Empty in a base-game save.

    No save with DLC has been decoded yet, so both shapes FileDB lists take are read: one child per entry,
    or one blob of 32-bit ids. Text that is neither is returned as it stands.
    """
    if node is None:
        return []
    if len(node):
        texts = [(c.findtext("id") or c.findtext("value") or c.text or "").strip() for c in node]
        return [h2i(t) if h2i(t) is not None else t for t in texts if t]
    text = (node.text or "").strip()
    if not text:
        return []
    if is_hex(text) and len(text) % 8 == 0:
        return [int.from_bytes(bytes.fromhex(text[i:i + 8]), "little") for i in range(0, len(text), 8)]
    return [text]


class Save:
    def __init__(self, dec: Decoded, names: Names):
        self.dec = dec
        self.n = names
        parser = ET.XMLParser(huge_tree=True)
        self.data = ET.parse(str(dec.xml("data")), parser).getroot()
        self.header = ET.parse(str(dec.xml("header")), parser).getroot()
        self.meta = ET.parse(str(dec.xml("meta")), parser).getroot()
        self._load()

    # ---------- header / meta ----------
    def _load(self):
        cp = self.header.find("CorporationProfile")
        if cp is None:
            raise ValueError(f"{self.dec.save.name}: header has no CorporationProfile; is this a normal game save?")
        self.total_money = h2i_hex((cp.findtext("TotalMoney") or "")[:8])
        self.total_population = h2i_hex((cp.findtext("TotalPopulation") or "")[:8])
        hist = cp.findall("SavegameHistory/None")
        self.changelist = h2i(hist[-1].findtext("Changelist")) if hist else None
        ts = h2i(self.meta.findtext("LastModTime"))
        self.saved_at = dt.datetime.fromtimestamp(ts) if ts else None
        self.save_name = self.meta.findtext("CorporationSaveGameName")
        self.game_time_ms = h2i(self.data.findtext("MetaGameManager/GameTotal"))
        self.active_dlcs = dlc_ids(self.header.find(".//GameSetup/ActiveDLCs"))
        self.participants = self._participants()
        self.relations = self._relations()
        self.money = self._money()
        self.max_population = self._max_population()
        self.sessions = self._sessions()
        self.snapshots = self._snapshots()
        self.ships = [sh for s in self.sessions for sh in s.ships]
        self.routes = self._routes()
        self.influence_used = self._influence()
        self.expeditions = self._expeditions()
        self.difficulty = self._difficulty()
        self.scores = self._scores()

    def _participants(self) -> dict[int, Participant]:
        out = {}
        for o in self.data.findall("MetaGameManager/MetaGameObjectManager/MetaObjectMap/None"):
            g = h2i(o.findtext("guid"))
            if o.find("MetaProfile") is None:
                continue
            pid = h2i(o.findtext("ParticipantID/id"), default=None)
            pid = HUMAN if pid is None and o.findtext("guid") == "41" else pid
            if pid is None:
                continue
            tmpl = self.n.template(g)
            if pid == HUMAN:
                kind, name = "human", utf16((o.findtext("MetaProfile/CustomCompanyName/None[2]") or ""))
                name = name or self.n(g)
            elif o.find("MetaConstructionAI") is not None:
                kind, name = "ai", self.n(g)
            elif "Pirate" in tmpl:
                kind, name = "pirate", self.n(g)
            elif "3rdParty" in tmpl and "Virtual" not in tmpl:
                kind, name = "third-party", self.n(g)
            else:
                kind, name = "other", self.n(g)
            out[pid] = Participant(pid, name, kind, h2i(o.findtext("MetaProfile/Level")), g)
        return out

    def _relations(self) -> dict[int, Relation]:
        out = {}
        for o in self.data.findall("MetaGameManager/MetaGameObjectManager/MetaObjectMap/None"):
            if o.findtext("guid") != "41":
                continue
            node = o.find("MetaDiplomacy/DiplomaticRelations")
            rel = list(node) if node is not None else []
            for k, v in zip(rel[0::2], rel[1::2]):
                other = h2i(k.findtext("value/id"))
                states = [h2i_hex(s.text) for s in v.findall("DiplomacyStates/None/State/id")]
                out[other] = Relation(other, h2i(v.findtext("Reputation")), states)
        return out

    def _money(self) -> int | None:
        for pair in self.data.findall("MetaGameManager/EconomyManager/Pair"):
            if h2i(pair.findtext("ParticipantID/value/id")) == HUMAN:
                for g, a in pairs_i32(pair.findtext("MetaEconomy/StrgLrg")):
                    if g == MONEY_GUID:
                        return a
        return None

    def _max_population(self) -> dict[int, int]:
        node = self.data.find("MetaGameManager/EconomyManager/MaxReachedPopulationCount")
        kids = list(node) if node is not None else []
        for k, v in zip(kids[0::2], kids[1::2]):
            if h2i(k.findtext("value/id")) == HUMAN:
                vals = [h2i_hex(x.text) for x in v.findall("None")]
                return dict(zip(vals[0::2], vals[1::2]))
        return {}

    # ---------- sessions / islands ----------
    def _sessions(self) -> list[Session]:
        out = []
        for s in self.data.findall("MetaGameManager/GameSessions/None"):
            gsm = s.find("SessionData/BinaryData/Content/GameSessionManager")
            if gsm is None:
                continue
            guid = h2i(s.findtext("SessionDesc/SessionGUID"))
            mt = gsm.find("MapTemplate")
            size = tuple(int(x) for x in (mt.findtext("Size") or "").split()) if mt is not None else None
            play = tuple(int(x) for x in (mt.findtext("PlayableArea") or "").split()) if mt is not None else None
            islands = [self._island(guid, gsm, k.text, v) for k, v in self._area_infos(gsm)]
            out.append(Session(guid, self.n(guid), size or None, play or None, islands,
                               h2i(gsm.findtext("SessionTotalTime")), self._ships(guid, gsm)))
        return out

    @staticmethod
    def _area_infos(gsm):
        kids = list(gsm.find("AreaInfo")) if gsm.find("AreaInfo") is not None else []
        return [(k, v) for k, v in zip(kids[0::2], kids[1::2]) if v.find("Fertility") is not None or v.find("CityNameGuid") is not None]

    def _island(self, sguid: int, gsm, area_id: str, info) -> Island:
        aid = int(area_id)
        owner = h2i_hex(info.findtext("Owner/id")) if info.find("Owner/id") is not None else None
        custom = info.findtext("CityName")
        name = utf16(custom) if custom else self.n(info.findtext("CityNameGuid"))
        ferts = [h2i(x.text) for x in info.findall("Fertility/None")]
        am = gsm.find(f"AreaManagers/AreaManager_{aid}")
        buildings, slots, xs, ys = [], collections.Counter(), [], []
        if am is not None:
            for o in am.findall("AreaObjectManager/GameObject/objects/None"):
                b = self._building(o)
                if b is None:
                    continue
                if b.template == "Slot":
                    slots[b.guid] += 1
                if b.pos:
                    xs.append(b.pos[0]); ys.append(b.pos[1])
                buildings.append(b)
        center = (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else None
        hap = {}
        hd = am.find("AreaHappinessManager/HappinessData") if am is not None else None
        if hd is not None:
            hap = {k: h2f(hd.findtext(k)) for k in ("avgHappiness", "pollution", "warfare", "attractiveness")}
            hap["citySizeIndex"] = h2i(hd.findtext("citySizeIndex"))
        isl = Island(sguid, aid, name, owner, ferts, slots, buildings, center, hap)
        for k, v in _kv(info.find("AreaEconomy/StorageTrends")):
            pts_node = v.find("Points")
            pts = [h2i_hex(p.text) or 0 for p in pts_node] if pts_node is not None else []
            if any(pts):
                isl.stock[h2i_hex(k.text, signed=False)] = StockTrend(pts, h2i(v.findtext("LastPointTime")) or 0, h2i(v.findtext("Estimation")))
        isl.passive_trade = self._passive_trade(info.find("PassiveTrade"))
        if am is not None:
            for k, v in _kv(am.find("AreaPopulationManager/NeedsUnlockMap")):
                isl.need_unlocked[h2i_hex(k.text)] = {h2i_hex(p.text, signed=False): (f.text or "").strip("0") != "" for p, f in _kv(v)}
            for k, v in _kv(am.find("AreaIncidentManager/incidentTypeData")):
                chance = h2f(v.findtext("AreaInfectionChance"))
                if chance is not None:
                    tid = h2i(k.findtext("value/id"), default=0)
                    isl.incident_chance[INCIDENT_TYPE.get(tid, f"type {tid}")] = chance
            if hd is not None:
                for k, v in _kv(hd.find("workingConditions")):
                    isl.working_conditions[h2i_hex(k.text)] = hexf(v.text, 0.0)
            isl.festival_active = am.findtext("AreaFestivalManager/isActive") == "True"
            running = {i.findtext("ID"): i for i in gsm.findall("IncidentManager/Incidents/None")}
            for k, v in _kv(am.find("AreaIncidentManager/incidentTypeData")):
                tid = h2i(k.findtext("value/id"), default=0)
                for ref in v.findall("Incidents/None"):   # incident ids of this type burning on this island
                    inc = running.get(ref.text)
                    if inc is not None:
                        isl.incidents.append({"type": INCIDENT_TYPE.get(tid, f"type {tid}"),
                                              "buildings": len(inc.findall("InfectedBuildings/None")),
                                              "started_at": self.n(inc.findtext("StartBuildingGUID"))})
        return isl

    def _passive_trade(self, pt) -> dict:
        """Harbour trade settings and the ledger of what visiting traders bought or sold here."""
        if pt is None:
            return {}
        offers = [{"good": h2i(o.findtext("GoodGuid")), "target": h2i(o.findtext("TargetAmount")),
                   "type": h2i_hex(o.findtext("Type/Type"), default=0)} for _, o in _kv(pt.find("OfferMap"))]
        min_stock = {h2i_hex(k.text, signed=False): h2i_hex(v.text) for k, v in _kv(pt.find("MinimumStock"))}
        purchases = []
        for k, entries in _kv(pt.find("History/PassiveTradeEntries")):
            trader = h2i(k.findtext("value/id"), default=0)
            for e in entries:
                goods = [(h2i(g.findtext("GoodGuid")), h2i(g.findtext("GoodAmount")) or 0) for g in e.findall("TradedGoods/None")]
                purchases.append({"trader": trader, "time_ms": h2i(e.findtext("ExecutionTime")) or 0,
                                  "coins": h2i(e.findtext("TotalPrice")) or 0,   # negative = the player paid
                                  "goods": [(g, a) for g, a in goods if g and g != MONEY_GUID]})
        purchases.sort(key=lambda x: x["time_ms"])
        # What the player's own route ships moved here: positive = unloaded onto the island, negative = loaded.
        route_entries = []
        for _, entries in _kv(pt.find("History/TradeRouteEntries")):
            for e in entries:
                if h2i_hex(e.findtext("Trader/id"), default=-1) != HUMAN:
                    continue
                route_entries.append({"route_id": h2i(e.findtext("RouteID")), "route_name": e.findtext("RouteName") or "",
                                      "time_ms": h2i(e.findtext("ExecutionTime")) or 0,
                                      "goods": [(h2i(g.findtext("GoodGuid")), h2i(g.findtext("GoodAmount")) or 0) for g in e.findall("TradedGoods/None")]})
        route_entries.sort(key=lambda x: x["time_ms"])
        return {"offers": offers, "min_stock": min_stock, "purchases": purchases, "route_entries": route_entries}

    def _ships(self, sguid: int, gsm) -> list[Ship]:
        """Every ship in the session. Ships sit in the ocean AreaManagers, not on islands."""
        out = []
        for o in gsm.iterfind("AreaManagers/*/AreaObjectManager/GameObject/objects/None"):
            tr = o.find("PropertyTradeRouteVehicle")
            if tr is None:
                continue
            g = h2i(o.findtext("guid"))
            b = self._building(o)
            cargo = []
            for slot in o.findall("ItemContainer/SlotContainer/SlotList/None"):
                cg = h2i(slot.findtext("Stack/None/GUID"))
                if cg:
                    cargo.append((cg, h2i(slot.findtext("ProductStackSize"), default=1)))
            dmg = h2i(o.findtext("Attackable/LastDamageTime"))
            dmg_owner = o.findtext("Attackable/LastDamageOwner/id")
            out.append(Ship(g, o.findtext("Nameable/VehicleName") or self.n(g), self.n(g), self.n.template(g),
                            HUMAN if b.owner is None else b.owner,   # the human's ships carry no ParticipantID
                            sguid, b.pos, h2i(o.findtext("MetaPersistent/MetaID")), h2i(tr.findtext("TradeRouteID")), cargo,
                            dmg if dmg is not None and dmg >= 0 else None,
                            h2i_hex(dmg_owner) if dmg_owner else None,
                            int(float(self.n.field(g, "ship_maintenance") or 0))))
        return out

    def _building(self, o) -> Building | None:
        g = h2i(o.findtext("guid"))
        if g is None:
            return None
        pos = None
        pt = o.findtext("Position")
        if pt:
            parts = pt.split()  # "x height z": map coordinates are x and z
            try:
                pos = (float(parts[0]), float(parts[2])) if len(parts) >= 3 else (float(parts[0]), float(parts[1]))
            except (ValueError, IndexError):
                pos = None
        # Decimal: the rule file decodes this field (24 = Princess Qing, matching her meta ParticipantID).
        # AreaInfo/Owner/id and LastDamageOwner/id are raw 2-byte hex instead.
        owner = h2i(o.findtext("ParticipantID/id"))
        b = Building(g, self.n(g), self.n.template(g), pos, owner, obj_id=h2i(o.findtext("ID")))
        b.icons = [x for x in (h2i(i.findtext("GUID")) for i in o.findall("Infolayer/Icons/None")) if x]
        b.storage = h2i(o.findtext("Warehouse/StorageCapacityRegistered"), default=0)
        b.queue = [(h2i(q.findtext("GUID")), hexf(q.findtext("RemainingTime"), 0.0), hexf(q.findtext("ConstructionTimeAtStart"), 0.0))
                   for q in o.findall("Shipyard/ConstructionQueue/None") if q.findtext("GUID")]
        b.blueprint = o.find("StateBits") is not None
        res = o.find("Residence7")
        if res is not None:
            b.residents = h2i(res.findtext("ResidentCount"), default=0)
            lvl = self.n.field(g, "population_level")
            b.tier = int(lvl) if lvl else None
            b.money_output = h2i(res.findtext("ProductMoneyOutput"))
            b.need_saturation = h2f(res.findtext("AverageNeedSaturation"))
            cs = res.find("ConsumptionStates")
            kids = list(cs) if cs is not None else []
            for k, v in zip(kids[0::2], kids[1::2]):
                sat = h2f(v.findtext("CurrentSaturation"))
                pg = h2i_hex(k.text)
                if pg is None:
                    continue
                if sat is not None:
                    b.needs[pg] = sat
                else:
                    # Empty state: the need is listed for the tier but has no consumption state. Seen for
                    # locked needs (Sewing Machines before the factory existed) and for Beer when the
                    # island ran dry (seen 2026-11-01). Not shown in needs, but the diff reports transitions.
                    b.needs_nostate.add(pg)
        fac = o.find("Factory7")
        if fac is not None:
            # ProductionState/Productivity tracks the running average; CurrentProductivity is often 0 for
            # free-area buildings (lumberjacks) even while they produce. Prefer the former when present.
            b.productivity = h2f(fac.findtext("ProductionState/Productivity"))
            if b.productivity is None:
                b.productivity = h2f(fac.findtext("CurrentProductivity"))
            b.current_productivity = h2f(fac.findtext("CurrentProductivity"))
            b.outputs = [int(x["Product"]) for x in (self.n.field(g, "outputs") or []) if "Product" in x]
        pr = o.findtext("Pausable/PausedReasons")
        b.paused = bool(pr and pr.strip("0"))
        if b.blueprint:
            return b  # blueprints have no upkeep, workforce or production
        for m in self.n.field(g, "maintenance") or []:
            if "Product" in m and "Amount" in m:
                prod = int(m["Product"])
                if b.paused:
                    # paused buildings need no workforce and pay the reduced coin upkeep
                    if prod != MONEY_GUID:
                        continue
                    amt = m.get("InactiveAmount") or m["Amount"]
                else:
                    amt = m["Amount"]
                b.maintenance[prod] = int(float(amt))
        return b

    # ---------- statistics snapshots ----------
    def _snapshots(self) -> dict[int, Snapshot]:
        out = {}
        ah = self.data.find("MetaGameManager/EconomyStatisticManager/History/areaHistory")
        if ah is None:
            return out
        kids = list(ah)

        def pairs(node):
            vals = [h2i(x.text) for x in node.findall("None")] if node is not None else []
            return {a: b for a, b in zip(vals[0::2], vals[1::2]) if a is not None}

        for k, v in zip(kids[0::2], kids[1::2]):
            aid = h2i_hex(k.text)
            snaps = v.findall("Snapshots/None")
            if not snaps:
                continue
            s = max(snaps, key=lambda x: h2i(x.findtext("CreationTime")) or 0)
            out[aid] = Snapshot(aid, h2i(s.findtext("CreationTime")) or 0, pairs(s.find("AmountPerPopulation")),
                                pairs(s.find("AmountPerProduct")), pairs(s.find("GenerationPerProduct")),
                                pairs(s.find("ConsumptionPerProduct")))
        return out

    def _routes(self) -> list[dict]:
        out = []
        rm = self.data.find("MetaGameManager/SessionTradeRouteManager/RouteMap")
        for r in (rm.findall("None") if rm is not None else []):
            if r.find("ID") is None:
                continue
            owner = h2i_hex(r.findtext("Owner/id"))
            stations = []
            for st in r.findall("Stations/None"):
                goods = []
                for gi in st.findall("GoodInfos/None"):
                    pg, amt = h2i(gi.findtext("ProductGUID") or gi.findtext("GoodGuid")), h2i(gi.findtext("Amount") or gi.findtext("GoodAmount"))
                    if pg:
                        # IsLoading is written only when false: absent = load here, "00" = unload here
                        goods.append((pg, amt, (gi.findtext("IsLoading") or "01").strip("0") != ""))
                stations.append({"area_id": h2i(st.findtext("AreaID")), "goods": goods})
            by_meta = {sh.meta_id: sh.name for sh in self.ships if sh.owner == owner}
            ships = [h2i(x) for x in (r.findtext("Ships") or "").split()]   # MetaPersistent/MetaID of each ship
            out.append({"ship_names": [by_meta.get(m, f"ship #{m} (not in a session: in transit?)") for m in ships],
                        "id": h2i(r.findtext("ID")), "name": utf16(r.findtext("Name")) if "\\x" in repr(r.findtext("Name") or "") else (r.findtext("Name") or ""),
                        "owner": owner, "ships": len(ships), "stations": stations})
        return out

    # ---------- company-wide extras ----------
    def _human_meta(self):
        for o in self.data.findall("MetaGameManager/MetaGameObjectManager/MetaObjectMap/None"):
            if o.findtext("guid") == "41":
                return o
        return None

    def _influence(self) -> dict[int, int]:
        """MetaInfluence/UsedAmount by category id. Category meanings are unknown; raw values only."""
        h = self._human_meta()
        node = h.find("MetaInfluence/UsedAmount") if h is not None else None
        return {h2i(k.findtext("value/id"), default=0): h2i_hex(v.text) for k, v in _kv(node)}

    def _expeditions(self) -> list[dict]:
        h = self._human_meta()
        node = h.find("MetaExpeditionUser/expeditions") if h is not None else None
        out = []
        for _, e in _kv(node):
            g = h2i(e.findtext("guid"))
            out.append({"guid": g, "name": self.n(g),
                        "threats": [self.n(int(t)) for t in (e.findtext("threats") or "").split()],
                        "ship": h2i(e.findtext("expeditionShip")) or None,     # 0 = no ship assigned yet
                        "offered_at_ms": h2i(e.findtext("startTimeStamp"))})
        return out

    def _difficulty(self) -> dict[str, int]:
        """Raw option ids from the header (Setup* settings). The id -> label mapping is not decoded."""
        node = self.header.find("CorporationProfile/Difficulty")
        out = {}
        for c in (node if node is not None else []):
            if c.tag.startswith("Setup"):
                out[c.tag[5:]] = h2i(c.findtext("value/id"), default=0)
        return out

    def _scores(self) -> dict[str, int]:
        h = self._human_meta()
        node = h.find("MetaPropertyHighscore/Highscore") if h is not None else None
        return {c.tag: h2i(c.text) for c in (node if node is not None else []) if len(c) == 0 and (c.text or "").lstrip("-").isdigit()}

    def snapshot_history(self, area_id: int) -> list[Snapshot]:
        """All statistics snapshots kept for one island (10 game minutes apart, about 12 of them), oldest first."""
        ah = self.data.find("MetaGameManager/EconomyStatisticManager/History/areaHistory")

        def pairs(node):
            vals = [h2i(x.text) for x in node.findall("None")] if node is not None else []
            return {a: b for a, b in zip(vals[0::2], vals[1::2]) if a is not None}

        for k, v in _kv(ah):
            if h2i_hex(k.text) == area_id:
                snaps = [Snapshot(area_id, h2i(s.findtext("CreationTime")) or 0, pairs(s.find("AmountPerPopulation")),
                                  pairs(s.find("AmountPerProduct")), pairs(s.find("GenerationPerProduct")),
                                  pairs(s.find("ConsumptionPerProduct"))) for s in v.findall("Snapshots/None")]
                return sorted(snaps, key=lambda s: s.time_ms)
        return []

    def nearest_island(self, session_guid: int, pos) -> tuple[Island, float] | None:
        best = None
        for s in self.sessions:
            if s.guid != session_guid:
                continue
            for i in s.islands:
                if i.center and pos:
                    d = math.dist(i.center, pos)
                    if best is None or d < best[1]:
                        best = (i, d)
        return best

    # ---------- derived ----------
    def human_islands(self) -> list[Island]:
        return [i for s in self.sessions for i in s.islands if i.owner == HUMAN]

    def island_by_area(self, aid: int) -> Island | None:
        for s in self.sessions:
            for i in s.islands:
                if i.area_id == aid:
                    return i
        return None

    def finances(self, island: Island) -> dict:
        """Approximate finance-screen numbers from building data: tax income and coin upkeep."""
        income = sum(b.money_output or 0 for b in island.residences())
        upkeep = sum(b.maintenance.get(MONEY_GUID, 0) for b in island.buildings if b.owner in (HUMAN, None) and b.residents is None)
        return {"tax_income": income, "upkeep": upkeep, "balance": income - upkeep}

    def company_finances(self) -> dict:
        """Tax minus building upkeep minus ship upkeep. Still an upper bound: ships crossing between sessions are
        not in the save's sessions, and whatever else the finance screen subtracts is not modelled."""
        fin = [self.finances(i) for i in self.human_islands()]
        ships = sum(sh.upkeep for sh in self.ships if sh.owner == HUMAN)
        tax, upkeep = sum(f["tax_income"] for f in fin), sum(f["upkeep"] for f in fin)
        return {"tax_income": tax, "upkeep": upkeep, "ship_upkeep": ships, "balance": tax - upkeep - ships}

    def workforce(self, island: Island) -> dict[int, dict]:
        """Per tier: available (= residents) vs demanded (sum of building workforce maintenance)."""
        pop = island.population()
        demand: collections.Counter = collections.Counter()
        for b in island.buildings:
            for prod, amt in b.maintenance.items():
                if prod in self.n.workforce_tier:
                    demand[self.n.workforce_tier[prod]] += amt
        out = {}
        for tier in self.n.tier_sorted(set(pop) | set(demand)):
            d = demand.get(tier, 0)
            if pop.get(tier) or d:
                out[tier] = {"available": pop.get(tier, 0), "demand": d}
        return out
