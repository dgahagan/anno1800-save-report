# Anno 1800 savegame format: what this project knows

Findings from decoding base-game saves of build 1412158. Each item says whether it was checked against the game screen. Paths are relative to `MetaGameManager` or to a session's `GameSessionManager` unless stated.

## Pipeline

- A `.a7s` file is an RDA container. RdaConsole unpacks it to `header`, `meta`, `gamesetup` and `data` blobs. Each is zlib-compressed FileDB.
- FileDBReader turns each blob into XML. The `a7s_all.xml` rule file from Anno1800SavegameVisualizer converts known leaf fields from hex to numbers and strings. It is a list of conversions by tag name, not a schema: a node it does not know stays as raw hex and breaks nothing.
- RdaConsole needs a trailing slash on `-o`. FileDBReader needs `NoWarn SYSLIB0011` on current .NET.
- One save decodes in about a minute to a `data.xml` of about 160 MB. lxml parses it in under 1 s with about 700 MB of memory.

## Hex or decimal

The same-looking field can be decimal in one place and raw hex in another. This caused two real bugs.

- Fields the rule file converts are decimal: object `ParticipantID/id`, `SessionGUID`, `TradeRouteID`.
- `AreaInfo/Owner/id` and `Attackable/LastDamageOwner/id` are raw 2-byte little-endian hex (`1800` = 24).
- An all-digit hex blob (`1000`) is ambiguous. `hexutil.h2i` treats all-digit text as decimal; `h2i_hex` and `hexf` always treat it as hex. Each call site must choose.

## Company level

- **Treasury:** `EconomyManager/Pair[ParticipantID=0]/MetaEconomy/StrgLrg`. GUID 1010017 = Coins. The layout is struct-of-arrays: all u32 keys, then all i32 values. It is not interleaved pairs. A second key (172; value -10 for the human, -1800 for AIs; meaning unknown) can appear. The header's `CorporationProfile/TotalMoney` and `TotalPopulation` match the decoded values.
- **Participants and diplomacy:** `MetaGameObjectManager/MetaObjectMap`. The human is the object with guid 41 (participant id 0). AIs carry `MetaConstructionAI`. Relations are under `MetaDiplomacy/DiplomaticRelations`. State 0 = war and 1 = peace are certain; 2 = trade rights and 3 = alliance are guesses.
- **Finance:** `SessionMoneyBalance`, `BalanceBeforeBudgets` and `TaxBalance` exist only under AI participants. There is no finance figure for the human in the save. The report estimates: residence `ProductMoneyOutput` minus building coin upkeep minus ship upkeep. That has run several hundred coins per minute above the game's finance screen.
- **Ship upkeep:** `ShipMaintenance/Maintenance` in `assets.xml` (Schooner 15, Gunboat 25, Clipper 175, Flagship 0).
- **Sessions:** `GameSessions/None`. Entries without a `GameSessionManager` are empty placeholders. Every save lists the SessionGUIDs of all sessions, DLC ones included.
- **DLC:** `ActiveDLCs` in `header.xml` and `gamesetup.xml`. Empty in a base-game save. Its encoding when filled is not yet known. DLC packs are `UplayProduct` assets.
- **Other company data, raw or unused:** `MetaInfluence/UsedAmount` (influence per category), `MetaExpeditionUser` (expeditions), `MetaPropertyHighscore` (scores such as `SessionIncomeScore`; meaning unverified), `CorporationProfile/DifficultyLevel` and `Difficulty/*`. Not read at all: `ResearchManager`, `TradeContractManager`, `SessionTransferManager`, `MetaBuildPermits`, `MetaBuffHandler`.

## Islands

- **Island list:** `AreaInfo` holds fertilities, owner and the city-name GUID. `AreaManagers/AreaManager_<id>` holds the objects: residences, factories, deposit slots. Unclaimed islands carry full fertility and deposit data.
- **Workforce** (verified against the island bar): per tier, residents minus the summed workforce upkeep of the buildings, paused buildings excluded. A tier's workforce good is the product in its `PopulationLevel7/PopulationOutputs` asset entry.
- **Blueprints:** an object with a `StateBits` child is a blueprint. It has no upkeep, workforce or production. `DelayedConstruction` is a component on fire and police stations, not a construction state.
- **Needs:** `ConsumptionStates` on a residence lists every potential need of the tier, bonus needs included. Each has a state (`CurrentSaturation`) or is empty. Empty means locked, or supply collapsed (verified in game: a good that ran dry lost its state while the need stayed switched on).
- **Need locks:** `AreaPopulationManager/NeedsUnlockMap` gives locked or unlocked per tier and good. It also marks bonus needs (`IsBonusNeed` in assets) as unlocked, so the report drops bonus needs before it calls a need "collapsed".
- **Switched-off needs (open):** `AreaResidenceConsumptionManager/ProductDistributionMap/.../DistributionPaused` looks like the per-tier consumption switch, but it also lists goods that were never switched off. Meaning unclear.
- **Live stock:** `AreaInfo/<area>/AreaEconomy/StorageTrends`. One point per 10 s (measured: 36 points between two saves 360 s apart), up to 120 points, fewer right after a load. The last point matched `AreaEconomy/StrgLrg` on one island. `Estimation` is the game's own trend figure; unit unverified. Not yet compared with a warehouse screen.
- **Storage capacity:** the sum of `Warehouse/StorageCapacityRegistered` over the trading post and depots. Plain warehouses register 0. It matched the fullest goods on three islands, not yet the number in the game UI.
- **Statistics history:** `EconomyStatisticManager/History/areaHistory`, keyed by u16 area id in hex. 12 snapshots, one per 10 minutes, with stock, generation and consumption per product. The statistics screen's production tab is tonnes per minute, current/max (verified on three building types).
- **Warning icons:** `Infolayer/Icons/GUID` on each object: Full Output Storage, Missing Goods, No Warehouse in Range, Fire! and others. "Awaiting promotion" sits on every house and is status, not a warning.
- **Incidents:** active ones are in the session `IncidentManager/Incidents`, joined by id to the island's `AreaIncidentManager/incidentTypeData/<type>/Incidents`. Risk is `AreaInfectionChance` per type. Type 0 = fire is confirmed by the icon. Types 1, 2, 5, 6 are inferred.
- **Mood:** `AreaHappinessManager/HappinessData/workingConditions` per tier; `AreaFestivalManager` for a running festival. A negative tier value matched one building of that tier at `Productivity` 1.5: the player had raised its per-building working-conditions slider (in-game text: "Your people dislike working overtime."). Not yet checked how the value scales with the number of such buildings.
- **Productivity:** the report uses `ProductionState/Productivity`. `CurrentProductivity` also exists. It is unclear which one the UI shows.

## Harbour trade

`AreaInfo/<area>/PassiveTrade`:

- `OfferMap`: `GoodGuid`, `TargetAmount`, `Type` (02 = standing buy order, filled by visiting trader ships).
- `MinimumStock`: pairs of hex guid and amount.
- `History/PassiveTradeEntries`: trader deals with `TotalPrice`. A rolling window: old deals drop out, so the total is not a lifetime figure.
- `History/TradeRouteEntries`: loads and unloads by the player's own routes, with `RouteName`. Positive = unloaded. It misses third-party harbours and some cross-session unloads, so a missing unload is not proof of no delivery.

## Ships and routes

- Ships are objects with `PropertyTradeRouteVehicle` in the ocean AreaManagers. Useful fields: `Nameable/VehicleName`, position, `TradeRouteID`, `ItemContainer/SlotContainer` (cargo), `Attackable/LastDamageTime` and `LastDamageOwner`.
- The human's ships carry no `ParticipantID/id`. AI and pirate ships carry their meta participant id.
- `MetaPersistent/MetaID` matches the entries in a route's `Ships`.
- A ship that is crossing between sessions is in no session. `SessionTransferManager` probably holds it; not decoded.
- Route goods carry `IsLoading` only when false: absent = load, `00` = unload. Repeated goods at one station are separate cargo slots (`Index`).
- The shipyard queue is `Shipyard/ConstructionQueue`.

## Map

- Ships leave a session from one fixed transfer point, not from the nearest border. For the New World (session 180025) the point toward the Old World is the map corner at (map width, 0). It is the right tip of the diamond minimap. Verified in game.
- The Old World's transfer point is not known. Neither are those of the DLC sessions.
- Pirate and third-party harbours are islands with no fertilities and no deposit slots.

## Open questions

- The encoding of `ActiveDLCs`, and everything about DLC sessions and buildings. No DLC save has been decoded.
- Whether airships carry `PropertyTradeRouteVehicle`.
- Whether the header `TotalPopulation` counts tourists.
- The remaining gap in the finance estimate. The next step is to note every line of the in-game finance screen once and compare.
- Incident type ids other than fire. Diplomacy states 2 and 3.
- The `DistributionPaused` switch.
- The Old World transfer point.
- The meaning of the raw influence, difficulty and score values.
