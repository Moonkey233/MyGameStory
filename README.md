# game-vault

中文文档: [README.zh-CN.md](README.zh-CN.md)

`game-vault` is a local Steam library data project. It keeps source data in simple, reviewable files and generates CSV views for Excel-style browsing and analysis. It does not use a SQL database.

## Data Model

- `data/manual/*.jsonl`: human-owned source data such as game identity, classifications, aliases, reviews, and purchases.
- `data/imports/`: raw imports from Steam API, screenshots, OCR, and local collection exports. Raw imports are never overwritten.
- `data/snapshots/*.jsonl`: parsed historical snapshots such as library ownership, playtime, achievements, and installed state.
- `data/suggestions/*.jsonl`: machine/OCR/import suggestions that need manual review before becoming official classifications.
- `data/derived/*.csv`: generated flat views and statistics. Do not edit these files by hand.

JSONL files intentionally contain no comments. Field notes and workflow rules live in this README and the scripts.

## Keys

Steam games use `game_id = "steam:<appid>"`, for example `steam:292030`. Game names, Chinese titles, English titles, Steam store titles, and OCR text are aliases only. They are not primary keys.

Category keys are stable English slugs such as `role_playing`. Legacy codes like `B.03` and display names are presentation metadata only.

## Steam Imports

First export Steam owned-library data. Credentials are read from environment variables (`STEAM_API_KEY`, `STEAM_ID`) or the ignored local file `scripts/steam_api.local.json`.

```powershell
python scripts/export_steam_owned.py
```

Then generate an import preview from the CSV:

```powershell
python scripts/import_steam_owned.py --input data\imports\steam_owned\steam_library_raw.csv --timestamp 20260625T203000+0800
```

Apply only after reviewing the preview. This owned-library importer writes only `data/manual/games.jsonl` and `data/snapshots/steam_owned_snapshot.jsonl`; it does not write classifications.

```powershell
python scripts/import_steam_owned.py --apply-preview data\imports\steam_owned\20260625T203000+0800.import_preview.json
```

The importer handles one source type per run. Steam owned-library imports update only owned-library related previews and snapshots.

## Screenshot OCR Imports

Generate OCR output and classification suggestions from one screenshot source at a time:

```powershell
python scripts/import_screenshot_ocr.py --image path\to\screenshot.png --timestamp 20260625T210000+0800
```

OCR is not a reliable source of truth. The script writes raw OCR metadata and suggestion previews. It does not overwrite official manual classifications.

For plain OCR text that already contains collection headers such as `B.01-动作战斗`, generate a classification preview:

```powershell
python scripts/import_classification_text.py --input data\imports\ocr\ocr_text.txt --timestamp 20260625T220000+0800
```

Use `--alias-map data\imports\ocr\alias_map.json` when Chinese OCR names need explicit appid mapping. The script writes safe matches to a classification preview and writes unresolved/conflicting records to review previews.

## Manual Classification

Official classifications live in `data/manual/classifications.jsonl`. Before writing to that file, generate a preview:

```powershell
python scripts/apply_confirmed_classifications.py --input confirmed_classifications.jsonl --timestamp 20260625T213000+0800
```

After reviewing the preview:

```powershell
python scripts/apply_confirmed_classifications.py --apply-preview data\manual\classifications.preview.20260625T213000+0800.jsonl
```

## Validate

```powershell
python scripts/validate_data.py
```

Validation checks JSONL syntax, `game_id` format, taxonomy references, duplicate manual records, favorite/state rules, and basic classification completeness warnings.

## Build CSV Views

```powershell
python scripts/build_views.py
```

This generates:

- `data/derived/games_flat.csv`
- `data/derived/category_stats.csv`
- `data/derived/backlog_stats.csv`

Do not manually edit files in `data/derived/`; rerun `build_views.py` instead.

## Privacy And Safety

- Do not commit Steam API keys. Use `.env` or environment variables.
- `.env`, raw imports, screenshots/OCR material, personal reviews, and purchase data are ignored by git by default.
- Scripts only read Steam client files when scanning installed state; they must not modify Steam client configuration.
- Raw import files are preserved and never deleted by scripts.
- Automatic classification suggestions go to `data/suggestions/` and require manual confirmation.
