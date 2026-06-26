from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from apply_confirmed_classifications import apply_preview as apply_classification_preview
from apply_confirmed_classifications import build_preview as build_classification_preview
from build_views import build_views
from export_steam_owned import export_owned_library
from import_steam_owned import apply_preview as apply_steam_preview
from import_steam_owned import build_preview as build_steam_preview
from validate_data import validate
from vaultlib import (
    TaxonomyChoice as CategoryChoice,
    format_taxonomy_choices,
    load_games_by_id,
    load_primary_category_choices,
    now_iso,
    parse_taxonomy_choice,
    read_json,
    read_jsonl,
    resolve_root,
    title_display,
    write_jsonl,
)


def default_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def load_category_choices(root: Path) -> list[CategoryChoice]:
    return load_primary_category_choices(root)


def parse_category_choice(raw_value: str, choices: list[CategoryChoice]) -> CategoryChoice | None:
    return parse_taxonomy_choice(raw_value, choices, allow_blank=True)


def format_category_help(choices: list[CategoryChoice]) -> str:
    return format_taxonomy_choices(choices)


def _latest_owned_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_key: dict[str, str] = {}
    path = root / "data" / "snapshots" / "steam_owned_snapshot.jsonl"
    for line_no, record in read_jsonl(path):
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            continue
        key = str(record.get("synced_at") or record.get("snapshot_id") or f"{line_no:012d}")
        if game_id not in latest or key >= latest_key[game_id]:
            latest[game_id] = record
            latest_key[game_id] = key
    return latest


def _minutes(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _hours_from_minutes(minutes: Any) -> float:
    value = _minutes(minutes) or 0
    return round(value / 60, 2)


def _new_games_from_preview(preview: dict[str, Any], known_game_ids: set[str]) -> list[dict[str, Any]]:
    games = [
        game
        for game in preview.get("games_to_upsert", [])
        if isinstance(game, dict) and isinstance(game.get("game_id"), str) and game["game_id"] not in known_game_ids
    ]
    return sorted(games, key=lambda game: title_display(game).casefold())


def _playtime_changes(preview: dict[str, Any], previous: dict[str, dict[str, Any]], known_game_ids: set[str]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for record in preview.get("steam_owned_snapshot_records", []):
        if not isinstance(record, dict):
            continue
        game_id = record.get("game_id")
        if not isinstance(game_id, str) or game_id not in known_game_ids:
            continue
        old_record = previous.get(game_id)
        if not old_record:
            continue
        old_minutes = _minutes(old_record.get("playtime_forever_min"))
        new_minutes = _minutes(record.get("playtime_forever_min"))
        if old_minutes is None or new_minutes is None or old_minutes == new_minutes:
            continue
        changes.append({
            "game_id": game_id,
            "steam_appid": record.get("steam_appid"),
            "steam_name": record.get("steam_name"),
            "old_minutes": old_minutes,
            "new_minutes": new_minutes,
            "delta_minutes": new_minutes - old_minutes,
        })
    return sorted(changes, key=lambda item: abs(int(item["delta_minutes"])), reverse=True)


def _classification_record(
    game: dict[str, Any],
    choice: CategoryChoice | None,
    timestamp: str,
    updated_at: str,
    default_curation_state: str,
) -> dict[str, Any]:
    game_id = str(game["game_id"])
    steam_appid = game.get("steam_appid")
    steam_name = title_display(game)
    return {
        "game_id": game_id,
        "primary_category": choice.key if choice else None,
        "classification_status": "confirmed" if choice else "pending",
        "curation_state": default_curation_state,
        "favorite": False,
        "special_flags": [],
        "manual_tags": [],
        "platform_plan": "steam",
        "play_plan": "later",
        "classification_evidence": [{
            "type": "manual_sync_prompt" if choice else "steam_sync_pending",
            "source": "update_steam_library.py",
            "timestamp": timestamp,
            "steam_appid": steam_appid,
            "steam_name": steam_name,
            "selected_category": choice.key if choice else None,
        }],
        "updated_at": updated_at,
    }


def prompt_classifications(
    new_games: list[dict[str, Any]],
    choices: list[CategoryChoice],
    timestamp: str,
    default_curation_state: str,
    input_func: Callable[[str], str] = input,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    updated_at = now_iso()
    skip_rest = False

    if new_games:
        print("\nB 类输入方式：数字 1-17、legacy code 如 B.04、或英文 slug。")
        print("直接回车 = 先标记为 pending；? = 重看类别；q = 剩余全部 pending。\n")
        print(format_category_help(choices))

    for index, game in enumerate(new_games, start=1):
        choice: CategoryChoice | None = None
        if not skip_rest:
            title = title_display(game)
            prompt = f"\n[{index}/{len(new_games)}] {title} ({game['game_id']}) 分类: "
            while True:
                raw_value = input_func(prompt)
                normalized = raw_value.strip().casefold()
                if normalized == "?":
                    print(format_category_help(choices))
                    continue
                if normalized in {"q", "quit"}:
                    skip_rest = True
                    choice = None
                    break
                try:
                    choice = parse_category_choice(raw_value, choices)
                    break
                except ValueError as exc:
                    print(f"{exc}；输入 ? 查看可选分类，直接回车可跳过。")
        records.append(_classification_record(game, choice, timestamp, updated_at, default_curation_state))
    return records


def _write_classification_input(root: Path, timestamp: str, records: list[dict[str, Any]]) -> Path:
    path = root / "data" / "imports" / "steam_owned" / f"{timestamp}.new_classifications.jsonl"
    if path.exists():
        raise FileExistsError(f"New-game classification input already exists: {path}")
    write_jsonl(path, records)
    return path


def _confirm_apply(yes: bool, input_func: Callable[[str], str] = input) -> bool:
    if yes:
        return True
    answer = input_func("\nApply previews now and rebuild derived CSVs? [Y/n] ").strip().casefold()
    return answer in {"", "y", "yes"}


def _print_sync_summary(new_games: list[dict[str, Any]], playtime_changes: list[dict[str, Any]]) -> None:
    print(f"\n新增游戏: {len(new_games)}")
    for game in new_games:
        print(f"  - {title_display(game)} ({game['game_id']})")

    print(f"\n游玩时长变化: {len(playtime_changes)}")
    for change in playtime_changes[:15]:
        delta_hours = round(int(change["delta_minutes"]) / 60, 2)
        old_hours = _hours_from_minutes(change["old_minutes"])
        new_hours = _hours_from_minutes(change["new_minutes"])
        print(f"  - {change['steam_name']} ({change['game_id']}): {old_hours}h -> {new_hours}h ({delta_hours:+}h)")
    if len(playtime_changes) > 15:
        print(f"  ... {len(playtime_changes) - 15} more")


def run(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    timestamp = args.timestamp or default_timestamp()
    config_path = Path(args.config).resolve() if args.config else root / "scripts" / "steam_api.local.json"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else root / "data" / "imports" / "steam_owned"

    known_game_ids = set(load_games_by_id(root))
    previous_snapshot = _latest_owned_snapshot(root)

    print(f"Timestamp: {timestamp}")
    export_outputs = export_owned_library(root, timestamp, config_path, output_dir, args.timeout)
    print(f"Steam CSV: {export_outputs['csv']}")
    print(f"Steam raw JSON: {export_outputs['raw_json']}")

    steam_preview_path = build_steam_preview(root, export_outputs["csv"], timestamp)
    steam_preview = read_json(steam_preview_path)
    new_games = _new_games_from_preview(steam_preview, known_game_ids)
    playtime_changes = _playtime_changes(steam_preview, previous_snapshot, known_game_ids)
    _print_sync_summary(new_games, playtime_changes)

    classification_preview_path: Path | None = None
    if new_games and not args.no_classification_records:
        choices = load_category_choices(root)
        if args.skip_classification_prompt:
            updated_at = now_iso()
            classification_records = [
                _classification_record(game, None, timestamp, updated_at, args.default_curation_state)
                for game in new_games
            ]
        else:
            classification_records = prompt_classifications(new_games, choices, timestamp, args.default_curation_state)
        classification_input_path = _write_classification_input(root, timestamp, classification_records)
        classification_preview_path = build_classification_preview(root, classification_input_path, timestamp)
        print(f"\nNew-game classification input: {classification_input_path}")
        print(f"Classification preview: {classification_preview_path}")

    print(f"\nSteam import preview: {steam_preview_path}")
    if args.preview_only:
        print("Preview-only mode: no source files were updated.")
        return 0
    if not _confirm_apply(args.yes):
        print("Stopped before applying previews.")
        return 0

    apply_steam_preview(root, steam_preview_path)
    print("Applied Steam owned-library preview.")
    if classification_preview_path:
        apply_classification_preview(root, classification_preview_path)
        print("Applied new-game classification preview.")

    outputs = build_views(root)
    print("Rebuilt derived CSVs:")
    for name, path in outputs.items():
        print(f"  - {name}: {path}")

    errors, warnings = validate(root)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"Update completed with validation errors: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    print(f"Validation passed: 0 error(s), {len(warnings)} warning(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-command Steam owned-library update with new-game classification prompts."
    )
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--timestamp", default=None, help="Timestamp for raw exports, previews, and snapshot IDs.")
    parser.add_argument("--config", default=None, help="Local JSON config with api_key and steam_id.")
    parser.add_argument("--output-dir", default=None, help="Output directory for raw JSON and CSV.")
    parser.add_argument("--timeout", type=int, default=30, help="Steam API timeout in seconds.")
    parser.add_argument("--default-curation-state", default="unstarted", help="Curation state used for new games.")
    parser.add_argument("--skip-classification-prompt", action="store_true", help="Create pending records for new games without prompting.")
    parser.add_argument("--no-classification-records", action="store_true", help="Do not create classification records for new games.")
    parser.add_argument("--preview-only", action="store_true", help="Generate exports and previews, then stop before applying.")
    parser.add_argument("--yes", action="store_true", help="Apply generated previews without the final confirmation prompt.")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
