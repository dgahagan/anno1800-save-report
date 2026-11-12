# Contributing

Bug reports, field checks against the game and pull requests are welcome.

## The most useful help

- **Check a number against the game.** The [Known limits](README.md#known-limits) list values that are estimated or guessed. Compare one with the game screen and report the result, with the game build.
- **Run it on a DLC save.** No save with DLC content has been decoded yet. A report from such a save tells us which parts work.
- **Decode a new field.** `docs/save-format.md` lists what is known about the save format and what is still open.

## Development

```bash
scripts/setup_tools.sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -rs
```

`tests/test_units.py` needs no save; CI runs it. `tests/test_report.py` runs against your own saves: copy `tests/fixtures.example.toml` to `tests/fixtures.local.toml` and list them. Pin values that you read off the game screens, not values from the report.

`CLAUDE.md` has a map of the code.

## Rules

- **Privacy.** Savegames and decoded XML hold the player's Ubisoft account id. Never commit them, and never attach them to an issue or pull request. Use invented names in examples.
- **Read-only.** The tool never writes to the game's folders. Saves are copied into `cache/` before they are decoded.
- **Third-party tools.** The anno-mods decoders declare no licence. `scripts/setup_tools.sh` builds them into the gitignored `vendor/`. Never commit their source or binaries.
- **No game data.** Nothing extracted from the game install ships with this repo.
