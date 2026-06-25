from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from vaultlib import coerce_float, coerce_int, load_games, load_taxonomies, read_jsonl, resolve_root, title_display, write_csv


GAMES_FLAT_FIELDS = [
    "game_id",
    "steam_appid",
    "title_display",
    "title_zh",
    "title_en",
    "platform",
    "primary_category",
    "primary_category_display",
    "legacy_b_code",
    "curation_state",
    "curation_state_display",
    "favorite",
    "installed",
    "multiplayer_candidate",
    "completion_target",
    "playtime_hours",
    "playtime_2weeks_hours",
    "last_played_at",
    "progress_percent",
    "achievement_percent",
    "rating_score",
    "rating_stars",
    "comment_short",
    "paid_price",
    "currency",
    "price_per_hour",
    "play_plan",
    "manual_tags",
    "notes",
]

CATEGORY_STATS_FIELDS = [
    "primary_category",
    "display_name",
    "count",
    "total_playtime_hours",
    "avg_rating",
    "completed_count",
    "unstarted_count",
]

BACKLOG_STATS_FIELDS = [
    "curation_state",
    "count",
    "total_estimated_hours",
    "total_playtime_hours",
]


def _records_by_game_id(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for _, record in read_jsonl(path):
        game_id = record.get("game_id")
        if isinstance(game_id, str):
            records[game_id] = record
    return records


def _latest_by_game_id(path: Path, timestamp_field: str = "synced_at") -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_key: dict[str, str] = {}
    for index, record in read_jsonl(path):
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            continue
        key = str(record.get(timestamp_field) or record.get("snapshot_id") or f"{index:012d}")
        if game_id not in latest or key >= latest_key[game_id]:
            latest[game_id] = record
            latest_key[game_id] = key
    return latest


def _hours(minutes: Any) -> str:
    value = coerce_float(minutes)
    if value is None:
        return ""
    return round(value / 60, 2)


def _achievement_percent(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    explicit = coerce_float(record.get("achievement_percent"))
    if explicit is not None:
        return round(explicit, 2)
    achieved = coerce_float(record.get("achieved_count"))
    total = coerce_float(record.get("total_count"))
    if achieved is None or total in (None, 0):
        return ""
    return round(achieved / total * 100, 2)


def _price_per_hour(paid_price: Any, playtime_hours: Any) -> str:
    price = coerce_float(paid_price)
    hours = coerce_float(playtime_hours)
    if price is None or hours in (None, 0):
        return ""
    return round(price / hours, 2)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "installed"}
    return bool(value)


def build_views(root: Path) -> dict[str, Path]:
    taxonomies = load_taxonomies(root)
    primary_categories = taxonomies["primary_categories"]
    curation_states = taxonomies["curation_states"]

    games = load_games(root)
    classifications = _records_by_game_id(root / "data" / "manual" / "classifications.jsonl")
    reviews = _records_by_game_id(root / "data" / "manual" / "personal_reviews.jsonl")
    purchases = _records_by_game_id(root / "data" / "manual" / "purchases.jsonl")
    library_snapshots = _latest_by_game_id(root / "data" / "snapshots" / "library_snapshot.jsonl")
    library_snapshots.update(_latest_by_game_id(root / "data" / "snapshots" / "steam_owned_snapshot.jsonl"))
    achievements = _latest_by_game_id(root / "data" / "snapshots" / "achievements_snapshot.jsonl")
    installed = _latest_by_game_id(root / "data" / "snapshots" / "installed_snapshot.jsonl")

    flat_rows: list[dict[str, Any]] = []
    for game in sorted(games, key=lambda item: title_display(item).casefold()):
        game_id = game.get("game_id")
        if not isinstance(game_id, str):
            continue
        titles = game.get("titles") if isinstance(game.get("titles"), dict) else {}
        classification = classifications.get(game_id, {})
        review = reviews.get(game_id, {})
        purchase = purchases.get(game_id, {})
        library = library_snapshots.get(game_id, {})
        installed_record = installed.get(game_id, {})
        achievement_record = achievements.get(game_id, {})

        category_key = classification.get("primary_category")
        category = primary_categories.get(category_key, {}) if category_key else {}
        curation_key = classification.get("curation_state")
        curation = curation_states.get(curation_key, {}) if curation_key else {}
        flags = classification.get("special_flags") or []
        playtime_hours = _hours(library.get("playtime_forever_min"))

        flat_rows.append({
            "game_id": game_id,
            "steam_appid": game.get("steam_appid", ""),
            "title_display": title_display(game),
            "title_zh": titles.get("zh_cn") or "",
            "title_en": titles.get("en") or "",
            "platform": game.get("platform", ""),
            "primary_category": category_key or "",
            "primary_category_display": category.get("display_name", ""),
            "legacy_b_code": category.get("legacy_code", ""),
            "curation_state": curation_key or "",
            "curation_state_display": curation.get("display_name", ""),
            "favorite": classification.get("favorite", False),
            "installed": _as_bool(installed_record.get("installed", False)),
            "multiplayer_candidate": "multiplayer_candidate" in flags,
            "completion_target": "completion_target" in flags,
            "playtime_hours": playtime_hours,
            "playtime_2weeks_hours": _hours(library.get("playtime_2weeks_min")),
            "last_played_at": library.get("last_played_at") or library.get("rtime_last_played") or "",
            "progress_percent": review.get("progress_percent", ""),
            "achievement_percent": _achievement_percent(achievement_record),
            "rating_score": review.get("rating_score", ""),
            "rating_stars": review.get("rating_stars", ""),
            "comment_short": review.get("comment_short", ""),
            "paid_price": purchase.get("paid_price", ""),
            "currency": purchase.get("currency", ""),
            "price_per_hour": _price_per_hour(purchase.get("paid_price"), playtime_hours),
            "play_plan": classification.get("play_plan", ""),
            "manual_tags": ";".join(classification.get("manual_tags") or []),
            "notes": purchase.get("note") or review.get("comment_long") or "",
        })

    category_rows: list[dict[str, Any]] = []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in flat_rows:
        category = row.get("primary_category")
        if category:
            by_category.setdefault(str(category), []).append(row)
    for category_key, rows in sorted(by_category.items()):
        ratings = [coerce_float(row.get("rating_score")) for row in rows]
        ratings = [rating for rating in ratings if rating is not None]
        category_rows.append({
            "primary_category": category_key,
            "display_name": primary_categories.get(category_key, {}).get("display_name", ""),
            "count": len(rows),
            "total_playtime_hours": round(sum(coerce_float(row.get("playtime_hours")) or 0 for row in rows), 2),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else "",
            "completed_count": sum(1 for row in rows if reviews.get(str(row["game_id"]), {}).get("completion_status") == "completed"),
            "unstarted_count": sum(1 for row in rows if reviews.get(str(row["game_id"]), {}).get("completion_status") == "not_started"),
        })

    backlog_rows: list[dict[str, Any]] = []
    by_curation: dict[str, list[dict[str, Any]]] = {}
    for row in flat_rows:
        curation = row.get("curation_state")
        if curation:
            by_curation.setdefault(str(curation), []).append(row)
    for curation_key, rows in sorted(by_curation.items()):
        backlog_rows.append({
            "curation_state": curation_key,
            "count": len(rows),
            "total_estimated_hours": "",
            "total_playtime_hours": round(sum(coerce_float(row.get("playtime_hours")) or 0 for row in rows), 2),
        })

    derived = root / "data" / "derived"
    outputs = {
        "games_flat": derived / "games_flat.csv",
        "category_stats": derived / "category_stats.csv",
        "backlog_stats": derived / "backlog_stats.csv",
    }
    write_csv(outputs["games_flat"], GAMES_FLAT_FIELDS, flat_rows)
    write_csv(outputs["category_stats"], CATEGORY_STATS_FIELDS, category_rows)
    write_csv(outputs["backlog_stats"], BACKLOG_STATS_FIELDS, backlog_rows)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Build generated CSV views from MyGameStory source data.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the MyGameStory repository root.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    outputs = build_views(root)
    for name, path in outputs.items():
        print(f"Generated {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
