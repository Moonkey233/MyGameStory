from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vaultlib import (
    coerce_int,
    load_games_by_id,
    now_iso,
    read_csv_dicts,
    read_json,
    read_jsonl,
    resolve_root,
    upsert_by_game_id,
    write_json,
    write_jsonl,
)


APPID_KEYS = ("steam_appid", "appid", "app_id", "AppID", "AppId", "appid64")
NAME_KEYS = ("name", "game_name", "steam_name", "Name", "Game", "title")
PLAYTIME_KEYS = ("playtime_forever", "playtime_forever_min", "PlaytimeForever", "playtime_minutes")
PLAYTIME_2W_KEYS = ("playtime_2weeks", "playtime_2weeks_min", "Playtime2Weeks")
LAST_PLAYED_KEYS = ("rtime_last_played", "last_played", "LastPlayed")


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _load_input(path: Path) -> tuple[str, list[dict[str, Any]]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return "csv", read_csv_dicts(path)
    payload = read_json(path)
    if isinstance(payload, dict):
        if isinstance(payload.get("response"), dict) and isinstance(payload["response"].get("games"), list):
            return "json", payload["response"]["games"]
        if isinstance(payload.get("games"), list):
            return "json", payload["games"]
    if isinstance(payload, list):
        return "json", payload
    raise ValueError(f"Unsupported Steam owned input shape: {path}")


def _epoch_to_iso(value: Any) -> str | None:
    timestamp = coerce_int(value)
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _merged_game_record(existing: dict[str, Any] | None, appid: int, name: str, snapshot_id: str, synced_at: str) -> dict[str, Any]:
    if not existing:
        return {
            "game_id": f"steam:{appid}",
            "platform": "steam",
            "steam_appid": appid,
            "type": "game",
            "titles": {
                "canonical": name,
                "zh_cn": None,
                "en": name,
            },
            "aliases": [name],
            "identity_source": {
                "source": "steam_owned_import",
                "snapshot_id": snapshot_id,
            },
            "created_at": synced_at,
            "updated_at": synced_at,
        }

    merged = dict(existing)
    titles = dict(merged.get("titles") or {})
    titles.setdefault("canonical", name)
    titles.setdefault("zh_cn", None)
    titles.setdefault("en", name)
    if not titles.get("canonical"):
        titles["canonical"] = name
    if not titles.get("en"):
        titles["en"] = name
    aliases = list(merged.get("aliases") or [])
    if name not in aliases:
        aliases.append(name)
    merged.update({
        "platform": "steam",
        "steam_appid": appid,
        "titles": titles,
        "aliases": aliases,
        "identity_source": {
            "source": "steam_owned_import",
            "snapshot_id": snapshot_id,
        },
        "updated_at": synced_at,
    })
    merged.setdefault("type", "game")
    merged.setdefault("created_at", synced_at)
    return merged


def _snapshot_record(record: dict[str, Any], appid: int, name: str, snapshot_id: str, synced_at: str) -> dict[str, Any]:
    rtime_last_played = coerce_int(_first(record, LAST_PLAYED_KEYS))
    return {
        "snapshot_id": snapshot_id,
        "game_id": f"steam:{appid}",
        "steam_appid": appid,
        "steam_name": name,
        "playtime_forever_min": coerce_int(_first(record, PLAYTIME_KEYS)),
        "playtime_2weeks_min": coerce_int(_first(record, PLAYTIME_2W_KEYS)),
        "rtime_last_played": rtime_last_played,
        "last_played_at": _epoch_to_iso(rtime_last_played),
        "source": "steam_owned_import",
        "synced_at": synced_at,
    }


def build_preview(root: Path, input_path: Path, timestamp: str) -> Path:
    input_format, records = _load_input(input_path)
    existing_games = load_games_by_id(root)
    synced_at = now_iso()
    snapshot_id = f"steam_owned:{timestamp}"

    games_to_upsert: list[dict[str, Any]] = []
    snapshot_records: list[dict[str, Any]] = []
    skipped_records: list[dict[str, Any]] = []

    for row in records:
        appid = coerce_int(_first(row, APPID_KEYS))
        name = _first(row, NAME_KEYS)
        if appid is None or not name:
            skipped_records.append({
                "raw_record": row,
                "reason": "missing_appid_or_name",
            })
            continue
        game_id = f"steam:{appid}"
        steam_name = str(name)
        games_to_upsert.append(_merged_game_record(existing_games.get(game_id), appid, steam_name, snapshot_id, synced_at))
        snapshot_records.append(_snapshot_record(row, appid, steam_name, snapshot_id, synced_at))

    preview = {
        "source": "steam_owned",
        "timestamp": timestamp,
        "input_path": str(input_path),
        "input_format": input_format,
        "snapshot_id": snapshot_id,
        "created_at": synced_at,
        "writes": {
            "games": "data/manual/games.jsonl",
            "steam_owned_snapshot": "data/snapshots/steam_owned_snapshot.jsonl",
        },
        "games_to_upsert": games_to_upsert,
        "steam_owned_snapshot_records": snapshot_records,
        "skipped_records": skipped_records,
    }
    output = root / "data" / "imports" / "steam_owned" / f"{timestamp}.import_preview.json"
    if output.exists():
        raise FileExistsError(f"Preview already exists: {output}")
    write_json(output, preview)
    return output


def apply_preview(root: Path, preview_path: Path) -> None:
    preview = read_json(preview_path)
    if preview.get("source") != "steam_owned":
        raise ValueError("Preview source is not steam_owned")

    games_path = root / "data" / "manual" / "games.jsonl"
    existing_games = [record for _, record in read_jsonl(games_path)]
    merged_games = upsert_by_game_id(existing_games, preview.get("games_to_upsert", []))
    write_jsonl(games_path, merged_games)

    snapshot_path = root / "data" / "snapshots" / "steam_owned_snapshot.jsonl"
    existing_snapshot_ids = {record.get("snapshot_id") for _, record in read_jsonl(snapshot_path)}
    snapshot_id = preview.get("snapshot_id")
    if snapshot_id in existing_snapshot_ids:
        raise ValueError(f"Snapshot {snapshot_id!r} already exists in {snapshot_path}")
    write_jsonl(snapshot_path, preview.get("steam_owned_snapshot_records", []), append=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Steam owned-library CSV/JSON through a preview, writing only games and owned snapshots."
    )
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--input", default=None, help="Steam owned library CSV or JSON input.")
    parser.add_argument("--timestamp", default=None, help="Timestamp used in preview and snapshot IDs.")
    parser.add_argument("--apply-preview", default=None, help="Apply a previously generated preview file.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    if args.apply_preview:
        apply_preview(root, Path(args.apply_preview))
        print(f"Applied preview: {args.apply_preview}")
        return 0

    if not args.input or not args.timestamp:
        parser.error("--input and --timestamp are required unless --apply-preview is used")
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    preview_path = build_preview(root, input_path, args.timestamp)
    print(f"Preview written: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
