# Anno 1800 companion

This project helps the user play Anno 1800. `anno-report` reads their savegames and writes reports, so Claude can advise on income, priorities, settling and diplomacy without screenshots.

## Read these first (the player's context lives in `notes/`, not in Claude's memory)
If `notes/` does not exist, copy `notes.example/` to `notes/` and ask the player the questions in `notes/player.md`.
- `notes/player.md` — who the player is, which DLC they own and how they want advice
- `notes/game-state.md` — current game summary; rewrite it after each advice session
- `notes/journal.md` — dated decisions; append an entry when a decision is reached and say so
- `notes/todo.md` — the current in-game checklist; tick items off as the player reports them done
- Give advice only about content the player owns. The report header lists the DLC active in the save.

`notes/`, `archive/`, `cache/`, `reports/` and `config.local.toml` are the player's data and are gitignored. When `ANNO_REPORT_HOME` is set they live in that folder, not next to the code.

## Workflow during a play session
1. `./anno-report` (newest save of the default profile; `--save PATH`, `--profile NAME`, `--trend N`, `--prune-cache N`). New save ≈ 1 min, cached ≈ 1 s.
2. Read `reports/latest.md` first, then `reports/islands.md` for settling questions, `reports/latest.json` for raw numbers.
3. Check the save time in the report header. If stale, ask the player to save (or wait for the next autosave).
4. Give advice; then update `notes/game-state.md` and append to `notes/journal.md`.

## Rules
- Never write to the game's folders. The save folder and game install are read-only. `extract.py` copies saves into `cache/` first.
- Savegames and decoded XML hold the player's Ubisoft account id. Never commit them and never paste them into an issue.
- anno-mods tools have no declared licence: built from source into gitignored `vendor/` by `scripts/setup_tools.sh`. Never commit their source or binaries.
- The rule file (`a7s_all.xml`) is read from `vendor/src/Anno1800SavegameVisualizer` (MIT).
- Python: project venv `.venv/`, package installed editable (`pip install -e ".[dev]"`). Run with `python -I`.
- .NET: tools are retargeted to the installed SDK by `setup_tools.sh`.
- Tests: `.venv/bin/pytest -rs`. `tests/test_units.py` needs no save. `tests/test_report.py` runs on each save in `tests/fixtures.local.toml` (pinned values live there) and skips when the save, the tools or the game is missing.

## Code map
- `anno_report/paths.py` — Steam/Proton discovery (all libraries in `libraryfolders.vdf`), profiles, `config.local.toml`; `ANNO_REPORT_HOME` moves the player's data to another folder
- `anno_report/extract.py` — RdaConsole + FileDBReader pipeline, cache by sha1
- `anno_report/gamedata.py` — GUID table from assets.xml/texts (names, needs, maintenance, outputs); `Names.tiers` and `Names.workforce_tier` come from the `PopulationLevel7` assets, so DLC tiers need no code change
- `anno_report/save.py` — object model: participants, relations, money, sessions, islands (live stock, need locks, incident risk, harbour trade), buildings (warning icons), ships, snapshots, routes, expeditions
- `anno_report/report.py` — markdown/json rendering; `problems()` is the "problems first" logic
- `anno_report/diff.py` — save-to-save changes
- `anno_report/cli.py` — entry point; archives unseen saves into `archive/<profile>/`; `--trend N` reads archive + live saves; `--prune-cache N` trims `cache/`
- `docs/save-format.md` — what is known about the save format, and what is still open

## Known unknowns (verify in-game before trusting)
- Verified: workforce balance per tier matches the island bar (paused buildings excluded).
- Finance figures are estimated: residence tax minus building upkeep minus ship upkeep (ships crossing between sessions are missed). The estimate has run several hundred coins per minute above the game. Upper bound only. The save holds no finance figures for the human, only for AIs.
- Factory productivity uses `ProductionState/Productivity`; `CurrentProductivity` is kept in JSON. Unclear which one the UI shows.
- Diplomacy state ids: 0 war, 1 peace are certain; 2 = trade rights and 3 = alliance are guesses.
- Island stock comes from `AreaEconomy/StorageTrends` (one point per 10 s, newest ≤10 s before the save). It matched the island `StrgLrg` values on one island only. Compare one warehouse screen with the report once.
- Incident type 0 = fire is confirmed by the "Fire!" icon. Riot, illness and explosion are inferred.
- "Needs unlocked but not consumed" cannot tell "ran dry" from "switched off". `ProductDistributionMap/DistributionPaused` may be the switch; its meaning is unclear.
- The Old World transfer point is unknown, so the `exit` column in islands.md is filled for the New World only.
- `influence_used_raw`, `difficulty_raw` and `scores_raw` in the JSON are raw ids and values with no decoded meaning.
- Ships crossing between sessions are in no session and are missing from the Ships table.
- The per-route harbour ledger is incomplete: third-party harbours and some cross-session unloads were not recorded. A missing unload is not proof of no delivery.
- Island storage cap = trading post + depots (`StorageCapacityRegistered`). It matched the fullest goods on three islands, not yet the number in the game UI.
- `ActiveDLCs` in the save header was empty in every save decoded so far. `save.dlc_ids()` reads two possible list shapes and names an id only when it is a `UplayProduct` asset. The real encoding is unverified until a save with DLC is decoded; expect `id N` in the report line until then.
- No save with DLC content has been decoded. DLC tiers, workforce and sessions come from the game data and should appear; DLC-only systems (electricity, research, docklands, tourists, airships, palace, irrigation, heat) are not modelled, and the finance estimate ignores them.
