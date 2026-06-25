from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vaultlib import JsonlError, is_valid_game_id, load_taxonomies, read_json, read_jsonl, resolve_root


MANUAL_FILES_WITH_UNIQUE_GAME_ID = [
    "games.jsonl",
    "classifications.jsonl",
    "aliases.jsonl",
    "personal_reviews.jsonl",
    "purchases.jsonl",
]

CLASSIFICATION_STATUS = {"pending", "suggested", "confirmed", "needs_review"}
COMPLETION_STATUS = {"not_started", "playing", "paused", "completed", "dropped", "endless", "unknown"}


def _jsonl_paths(root: Path) -> list[Path]:
    return sorted((root / "data").rglob("*.jsonl"))


def _load_jsonl_checked(path: Path, errors: list[str]) -> list[tuple[int, dict[str, Any]]]:
    try:
        return read_jsonl(path)
    except JsonlError as exc:
        errors.append(str(exc))
        return []


def _check_config(root: Path, errors: list[str]) -> None:
    for rel_path in [
        Path("config/taxonomies.json"),
        Path("config/schema_version.json"),
        Path("config/platforms.json"),
        Path("data/manual/tags.json"),
    ]:
        path = root / rel_path
        try:
            read_json(path)
        except FileNotFoundError:
            errors.append(f"{rel_path}: missing required JSON file")
        except json.JSONDecodeError as exc:
            errors.append(f"{rel_path}:{exc.lineno}: invalid JSON: {exc.msg}")


def _check_duplicate_game_ids(path: Path, records: list[tuple[int, dict[str, Any]]], errors: list[str]) -> None:
    seen: dict[str, int] = {}
    for line_no, record in records:
        game_id = record.get("game_id")
        if game_id is None:
            continue
        if not isinstance(game_id, str):
            errors.append(f"{path}:{line_no}: game_id must be a string")
            continue
        if game_id in seen:
            errors.append(f"{path}:{line_no}: duplicate game_id {game_id!r}; first seen on line {seen[game_id]}")
        else:
            seen[game_id] = line_no


def _check_game_id_fields(path: Path, records: list[tuple[int, dict[str, Any]]], errors: list[str]) -> None:
    for line_no, record in records:
        game_id = record.get("game_id")
        if game_id is not None and not is_valid_game_id(game_id):
            errors.append(f"{path}:{line_no}: invalid game_id {game_id!r}")


def _check_games(records: list[tuple[int, dict[str, Any]]], path: Path, errors: list[str]) -> set[str]:
    game_ids: set[str] = set()
    for line_no, record in records:
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            errors.append(f"{path}:{line_no}: game_id is required")
            continue
        game_ids.add(game_id)
        if record.get("platform") == "steam":
            if record.get("steam_appid") is None:
                errors.append(f"{path}:{line_no}: steam_appid is required for Steam games")
            if game_id.startswith("steam:") and str(record.get("steam_appid")) != game_id.split(":", 1)[1]:
                errors.append(f"{path}:{line_no}: steam_appid does not match game_id")
    return game_ids


def _check_classifications(
    records: list[tuple[int, dict[str, Any]]],
    path: Path,
    known_game_ids: set[str],
    taxonomies: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    primary_categories = taxonomies["primary_categories"]
    curation_states = taxonomies["curation_states"]
    special_flags = taxonomies["special_flags"]

    for line_no, record in records:
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            errors.append(f"{path}:{line_no}: game_id is required")
            continue
        if game_id not in known_game_ids:
            errors.append(f"{path}:{line_no}: classification game_id {game_id!r} does not exist in games.jsonl")

        status = record.get("classification_status")
        if status not in CLASSIFICATION_STATUS:
            errors.append(f"{path}:{line_no}: classification_status must be one of {sorted(CLASSIFICATION_STATUS)}")

        primary_category = record.get("primary_category")
        if primary_category is not None and primary_category not in primary_categories:
            errors.append(f"{path}:{line_no}: unknown primary_category {primary_category!r}")

        curation_state = record.get("curation_state")
        if curation_state is not None and curation_state not in curation_states:
            errors.append(f"{path}:{line_no}: unknown curation_state {curation_state!r}")

        favorite = record.get("favorite", False)
        if not isinstance(favorite, bool):
            errors.append(f"{path}:{line_no}: favorite must be a boolean")
        if favorite is True and curation_state != "handpicked":
            errors.append(f"{path}:{line_no}: favorite=true requires curation_state='handpicked'")

        flags = record.get("special_flags", [])
        if not isinstance(flags, list):
            errors.append(f"{path}:{line_no}: special_flags must be a list")
        else:
            for flag in flags:
                if flag not in special_flags:
                    errors.append(f"{path}:{line_no}: unknown special_flag {flag!r}")

        if primary_category is None:
            if curation_state == "card_stock":
                warnings.append(f"{path}:{line_no}: card_stock game {game_id!r} has no primary_category")
            else:
                warnings.append(f"{path}:{line_no}: non-card_stock game {game_id!r} has no primary_category")


def _check_reviews(records: list[tuple[int, dict[str, Any]]], path: Path, errors: list[str]) -> None:
    for line_no, record in records:
        completion_status = record.get("completion_status")
        if completion_status is not None and completion_status not in COMPLETION_STATUS:
            errors.append(f"{path}:{line_no}: unknown completion_status {completion_status!r}")
        progress = record.get("progress_percent")
        if progress is not None and not (isinstance(progress, (int, float)) and 0 <= progress <= 100):
            errors.append(f"{path}:{line_no}: progress_percent must be 0-100 or null")
        rating_score = record.get("rating_score")
        if rating_score is not None and not (isinstance(rating_score, (int, float)) and 0 <= rating_score <= 100):
            errors.append(f"{path}:{line_no}: rating_score must be 0-100 or null")
        rating_stars = record.get("rating_stars")
        if rating_stars is not None and not (isinstance(rating_stars, (int, float)) and 0 <= rating_stars <= 5):
            errors.append(f"{path}:{line_no}: rating_stars must be 0-5 or null")


def validate(root: Path, strict_warnings: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    _check_config(root, errors)

    jsonl_records: dict[Path, list[tuple[int, dict[str, Any]]]] = {}
    for path in _jsonl_paths(root):
        records = _load_jsonl_checked(path, errors)
        jsonl_records[path] = records
        _check_game_id_fields(path, records, errors)

    manual_root = root / "data" / "manual"
    for filename in MANUAL_FILES_WITH_UNIQUE_GAME_ID:
        path = manual_root / filename
        _check_duplicate_game_ids(path, jsonl_records.get(path, []), errors)

    games_path = manual_root / "games.jsonl"
    known_game_ids = _check_games(jsonl_records.get(games_path, []), games_path, errors)

    try:
        taxonomies = load_taxonomies(root)
    except Exception as exc:  # noqa: BLE001 - validation should report config failures compactly.
        errors.append(f"config/taxonomies.json: cannot load taxonomy: {exc}")
        taxonomies = {"primary_categories": {}, "curation_states": {}, "special_flags": {}}

    classifications_path = manual_root / "classifications.jsonl"
    _check_classifications(
        jsonl_records.get(classifications_path, []),
        classifications_path,
        known_game_ids,
        taxonomies,
        errors,
        warnings,
    )

    reviews_path = manual_root / "personal_reviews.jsonl"
    _check_reviews(jsonl_records.get(reviews_path, []), reviews_path, errors)

    if strict_warnings and warnings:
        errors.extend(f"warning treated as error: {warning}" for warning in warnings)
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MyGameStory source data.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the MyGameStory repository root.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    errors, warnings = validate(root, strict_warnings=args.strict)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")

    if errors:
        print(f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    print(f"Validation passed: 0 error(s), {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
