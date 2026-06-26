from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vaultlib import (
    coerce_float,
    coerce_int,
    load_games,
    load_taxonomies,
    read_jsonl,
    resolve_root,
    taxonomy_display_order,
    title_display,
    write_csv,
    write_json,
)


def _records_by_game_id(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for _, record in read_jsonl(path):
        game_id = record.get("game_id")
        if isinstance(game_id, str):
            records[game_id] = record
    return records


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return datetime.fromtimestamp(value).astimezone()
    text = str(value).strip()
    if not text or text == "0":
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()
    except ValueError:
        epoch = coerce_int(text)
        if epoch and epoch > 0:
            return datetime.fromtimestamp(epoch).astimezone()
    return None


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _epoch_start() -> datetime:
    return datetime(1970, 1, 1, tzinfo=datetime.now().astimezone().tzinfo)


def _round(value: float | int | None, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _hours(minutes: Any) -> float:
    value = coerce_float(minutes)
    return _round(value / 60) if value is not None else 0.0


def _avg(values: list[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _median(values: list[float]) -> float:
    return _round(statistics.median(values)) if values else 0.0


def _percent(part: int | float, total: int | float) -> float:
    return _round(float(part) / float(total) * 100) if total else 0.0


def _latest_by_game_id(records: list[dict[str, Any]], timestamp_field: str = "synced_at") -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_key: dict[str, str] = {}
    for index, record in enumerate(records):
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            continue
        key = str(record.get(timestamp_field) or record.get("snapshot_id") or f"{index:012d}")
        if game_id not in latest or key >= latest_key[game_id]:
            latest[game_id] = record
            latest_key[game_id] = key
    return latest


def _load_snapshot_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in [
        root / "data" / "snapshots" / "library_snapshot.jsonl",
        root / "data" / "snapshots" / "steam_owned_snapshot.jsonl",
    ]:
        records.extend(record for _, record in read_jsonl(path))
    return records


def _snapshot_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        snapshot_id = record.get("snapshot_id")
        if isinstance(snapshot_id, str):
            grouped[snapshot_id].append(record)

    groups: list[dict[str, Any]] = []
    for snapshot_id, items in grouped.items():
        synced_values = [_parse_dt(item.get("synced_at")) for item in items]
        synced_values = [item for item in synced_values if item]
        groups.append({
            "snapshot_id": snapshot_id,
            "synced_at": max(synced_values) if synced_values else None,
            "records": items,
        })
    return sorted(groups, key=lambda item: (_iso(item["synced_at"]), item["snapshot_id"]))


def _snapshot_change_report(
    root: Path,
    games_by_id: dict[str, dict[str, Any]],
    snapshot_records: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    groups = _snapshot_groups(snapshot_records)
    if not groups:
        return {
            "snapshot_count": 0,
            "latest_snapshot_id": "",
            "latest_synced_at": "",
            "latest_game_count": 0,
            "previous_snapshot_id": "",
            "added_since_previous": [],
            "removed_since_previous": [],
            "top_playtime_deltas": [],
        }

    latest = groups[-1]
    latest_by_id = {record["game_id"]: record for record in latest["records"] if isinstance(record.get("game_id"), str)}
    previous = groups[-2] if len(groups) >= 2 else None
    previous_by_id = {
        record["game_id"]: record
        for record in previous["records"]
        if previous and isinstance(record.get("game_id"), str)
    } if previous else {}

    added = sorted(set(latest_by_id) - set(previous_by_id), key=lambda game_id: title_display(games_by_id.get(game_id)).casefold())
    removed = sorted(set(previous_by_id) - set(latest_by_id), key=lambda game_id: title_display(games_by_id.get(game_id)).casefold())
    deltas: list[dict[str, Any]] = []
    for game_id in sorted(set(latest_by_id) & set(previous_by_id)):
        old_minutes = coerce_int(previous_by_id[game_id].get("playtime_forever_min"))
        new_minutes = coerce_int(latest_by_id[game_id].get("playtime_forever_min"))
        if old_minutes is None or new_minutes is None or new_minutes == old_minutes:
            continue
        game = games_by_id.get(game_id, {})
        deltas.append({
            "game_id": game_id,
            "steam_appid": game.get("steam_appid", latest_by_id[game_id].get("steam_appid")),
            "title": title_display(game) or latest_by_id[game_id].get("steam_name", ""),
            "old_playtime_hours": _hours(old_minutes),
            "new_playtime_hours": _hours(new_minutes),
            "delta_hours": _round((new_minutes - old_minutes) / 60),
        })
    deltas.sort(key=lambda item: abs(float(item["delta_hours"])), reverse=True)

    def row(game_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        game = games_by_id.get(game_id, {})
        return {
            "game_id": game_id,
            "steam_appid": game.get("steam_appid", snapshot.get("steam_appid")),
            "title": title_display(game) or snapshot.get("steam_name", ""),
            "playtime_hours": _hours(snapshot.get("playtime_forever_min")),
            "last_played_at": snapshot.get("last_played_at") or "",
        }

    return {
        "snapshot_count": len(groups),
        "latest_snapshot_id": latest["snapshot_id"],
        "latest_synced_at": _iso(latest["synced_at"]),
        "latest_game_count": len(latest_by_id),
        "previous_snapshot_id": previous["snapshot_id"] if previous else "",
        "added_since_previous": [row(game_id, latest_by_id[game_id]) for game_id in added[:limit]],
        "removed_since_previous": [row(game_id, previous_by_id[game_id]) for game_id in removed[:limit]],
        "top_playtime_deltas": deltas[:limit],
    }


def _game_row(
    game: dict[str, Any],
    classification: dict[str, Any],
    library: dict[str, Any],
    review: dict[str, Any],
    purchase: dict[str, Any],
) -> dict[str, Any]:
    titles = game.get("titles") if isinstance(game.get("titles"), dict) else {}
    flags = classification.get("special_flags") if isinstance(classification.get("special_flags"), list) else []
    last_played = _parse_dt(library.get("last_played_at") or library.get("rtime_last_played"))
    return {
        "game_id": game.get("game_id", ""),
        "steam_appid": game.get("steam_appid", ""),
        "title": title_display(game),
        "title_zh": titles.get("zh_cn") or "",
        "title_en": titles.get("en") or "",
        "platform": game.get("platform", ""),
        "type": game.get("type", ""),
        "created_at": game.get("created_at", ""),
        "updated_at": game.get("updated_at", ""),
        "primary_category": classification.get("primary_category") or "",
        "classification_status": classification.get("classification_status") or "",
        "curation_state": classification.get("curation_state") or "",
        "favorite": bool(classification.get("favorite", False)),
        "special_flags": flags,
        "manual_tags": classification.get("manual_tags") or [],
        "playtime_hours": _hours(library.get("playtime_forever_min")),
        "playtime_2weeks_hours": _hours(library.get("playtime_2weeks_min")),
        "last_played_at": _iso(last_played),
        "last_played_dt": last_played,
        "rating_score": coerce_float(review.get("rating_score")),
        "rating_stars": coerce_float(review.get("rating_stars")),
        "completion_status": review.get("completion_status") or "",
        "progress_percent": coerce_float(review.get("progress_percent")),
        "paid_price": coerce_float(purchase.get("paid_price")),
        "currency": purchase.get("currency") or "",
    }


def _category_rows(rows: list[dict[str, Any]], taxonomies: dict[str, Any], recent_cutoff: datetime) -> list[dict[str, Any]]:
    categories = taxonomies["primary_categories"]
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[row["primary_category"] or "unclassified"].append(row)

    output: list[dict[str, Any]] = []
    def category_sort_key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, str]:
        category_key = item[0]
        if category_key == "unclassified":
            return (9999, category_key)
        return (taxonomy_display_order(categories.get(category_key, {})), category_key)

    for category_key, items in sorted(by_category.items(), key=category_sort_key):
        playtimes = [float(item["playtime_hours"]) for item in items]
        played = [item for item in items if float(item["playtime_hours"]) > 0]
        recent = [item for item in items if item["last_played_dt"] and item["last_played_dt"] >= recent_cutoff]
        ratings = [float(item["rating_score"]) for item in items if item["rating_score"] is not None]
        category = categories.get(category_key, {}) if category_key != "unclassified" else {}
        output.append({
            "primary_category": category_key,
            "legacy_code": category.get("legacy_code", ""),
            "display_name": category.get("display_name", "未分类"),
            "count": len(items),
            "library_percent": _percent(len(items), len(rows)),
            "played_count": len(played),
            "unplayed_count": len(items) - len(played),
            "recently_played_count": len(recent),
            "total_playtime_hours": _round(sum(playtimes)),
            "avg_playtime_hours": _avg(playtimes),
            "median_playtime_hours": _median(playtimes),
            "avg_rating": _avg(ratings),
        })
    return output


def _counter_rows(counter: Counter[str], key_name: str, total: int) -> list[dict[str, Any]]:
    return [
        {key_name: key or "blank", "count": count, "percent": _percent(count, total)}
        for key, count in counter.most_common()
    ]


def build_report(root: Path, limit: int = 20, recent_days: int = 30) -> dict[str, Any]:
    taxonomies = load_taxonomies(root)
    games = load_games(root)
    games_by_id = {game["game_id"]: game for game in games if isinstance(game.get("game_id"), str)}
    classifications = _records_by_game_id(root / "data" / "manual" / "classifications.jsonl")
    reviews = _records_by_game_id(root / "data" / "manual" / "personal_reviews.jsonl")
    purchases = _records_by_game_id(root / "data" / "manual" / "purchases.jsonl")
    snapshot_records = _load_snapshot_records(root)
    library = _latest_by_game_id(snapshot_records)

    rows = [
        _game_row(
            game,
            classifications.get(str(game.get("game_id")), {}),
            library.get(str(game.get("game_id")), {}),
            reviews.get(str(game.get("game_id")), {}),
            purchases.get(str(game.get("game_id")), {}),
        )
        for game in games
        if isinstance(game.get("game_id"), str)
    ]
    now = datetime.now().astimezone()
    recent_cutoff = now - timedelta(days=recent_days)

    playtimes = [float(row["playtime_hours"]) for row in rows]
    played_rows = [row for row in rows if float(row["playtime_hours"]) > 0]
    classified_rows = [row for row in rows if row["primary_category"]]
    favorite_rows = [row for row in rows if row["favorite"]]
    recent_played_rows = [row for row in rows if row["last_played_dt"] and row["last_played_dt"] >= recent_cutoff]
    ratings = [float(row["rating_score"]) for row in rows if row["rating_score"] is not None]
    prices = [float(row["paid_price"]) for row in rows if row["paid_price"] is not None]

    category_rows = _category_rows(rows, taxonomies, recent_cutoff)
    category_counter = Counter(row["primary_category"] or "unclassified" for row in rows)
    status_counter = Counter(row["classification_status"] or "blank" for row in rows)
    curation_counter = Counter(row["curation_state"] or "blank" for row in rows)
    type_counter = Counter(row["type"] or "blank" for row in rows)
    platform_counter = Counter(row["platform"] or "blank" for row in rows)
    completion_counter = Counter(row["completion_status"] or "blank" for row in rows)
    flag_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    for row in rows:
        flag_counter.update(str(flag) for flag in row["special_flags"])
        tag_counter.update(str(tag) for tag in row["manual_tags"])

    epoch_start = _epoch_start()
    recently_added = sorted(rows, key=lambda row: _parse_dt(row["created_at"]) or epoch_start, reverse=True)[:limit]
    recently_updated = sorted(rows, key=lambda row: _parse_dt(row["updated_at"]) or epoch_start, reverse=True)[:limit]
    recently_played = sorted([row for row in rows if row["last_played_dt"]], key=lambda row: row["last_played_dt"], reverse=True)[:limit]
    top_playtime = sorted(rows, key=lambda row: float(row["playtime_hours"]), reverse=True)[:limit]
    zero_playtime = sorted([row for row in rows if float(row["playtime_hours"]) == 0], key=lambda row: row["title"].casefold())[:limit]
    pending = sorted(
        [row for row in rows if row["classification_status"] in {"pending", "needs_review", "suggested"} or not row["primary_category"]],
        key=lambda row: (row["classification_status"], row["title"].casefold()),
    )[:limit]

    total_paid_by_currency: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["paid_price"] is not None:
            total_paid_by_currency[row["currency"] or "blank"] += float(row["paid_price"])

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "recent_days": recent_days,
        "limit": limit,
        "summary": {
            "total_games": len(rows),
            "platform_counts": _counter_rows(platform_counter, "platform", len(rows)),
            "type_counts": _counter_rows(type_counter, "type", len(rows)),
            "defined_primary_categories": len(taxonomies["primary_categories"]),
            "used_primary_categories": len([key for key in category_counter if key != "unclassified"]),
            "classified_games": len(classified_rows),
            "unclassified_games": len(rows) - len(classified_rows),
            "classification_coverage_percent": _percent(len(classified_rows), len(rows)),
            "favorite_games": len(favorite_rows),
            "played_games": len(played_rows),
            "unplayed_games": len(rows) - len(played_rows),
            "played_percent": _percent(len(played_rows), len(rows)),
            "recently_played_games": len(recent_played_rows),
            "total_playtime_hours": _round(sum(playtimes)),
            "avg_playtime_hours_all_games": _avg(playtimes),
            "avg_playtime_hours_played_games": _avg([float(row["playtime_hours"]) for row in played_rows]),
            "median_playtime_hours_all_games": _median(playtimes),
            "median_playtime_hours_played_games": _median([float(row["playtime_hours"]) for row in played_rows]),
            "total_2weeks_playtime_hours": _round(sum(float(row["playtime_2weeks_hours"]) for row in rows)),
            "rated_games": len(ratings),
            "avg_rating_score": _avg(ratings),
            "paid_records": len(prices),
            "total_paid_by_currency": {key: _round(value) for key, value in sorted(total_paid_by_currency.items())},
        },
        "category_stats": category_rows,
        "classification_status_stats": _counter_rows(status_counter, "classification_status", len(rows)),
        "curation_state_stats": _counter_rows(curation_counter, "curation_state", len(rows)),
        "special_flag_stats": _counter_rows(flag_counter, "special_flag", max(sum(flag_counter.values()), 1)),
        "manual_tag_stats": _counter_rows(tag_counter, "manual_tag", max(sum(tag_counter.values()), 1))[:limit],
        "completion_status_stats": _counter_rows(completion_counter, "completion_status", len(rows)),
        "recently_added": [_public_row(row) for row in recently_added],
        "recently_updated": [_public_row(row) for row in recently_updated],
        "recently_played": [_public_row(row) for row in recently_played],
        "top_playtime": [_public_row(row) for row in top_playtime],
        "zero_playtime": [_public_row(row) for row in zero_playtime],
        "pending_or_unclassified": [_public_row(row) for row in pending],
        "snapshot_changes": _snapshot_change_report(root, games_by_id, snapshot_records, limit),
    }


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "game_id": row["game_id"],
        "steam_appid": row["steam_appid"],
        "title": row["title"],
        "primary_category": row["primary_category"],
        "classification_status": row["classification_status"],
        "curation_state": row["curation_state"],
        "playtime_hours": row["playtime_hours"],
        "playtime_2weeks_hours": row["playtime_2weeks_hours"],
        "last_played_at": row["last_played_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "rating_score": row["rating_score"] if row["rating_score"] is not None else "",
        "completion_status": row["completion_status"],
    }


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_None_\n"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def format_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    changes = report["snapshot_changes"]
    lines = [
        "# MyGameStory Stats",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Total games: {summary['total_games']}",
        f"- Classification coverage: {summary['classified_games']} / {summary['total_games']} ({summary['classification_coverage_percent']}%)",
        f"- Used / defined categories: {summary['used_primary_categories']} / {summary['defined_primary_categories']}",
        f"- Played games: {summary['played_games']} / {summary['total_games']} ({summary['played_percent']}%)",
        f"- Total playtime: {summary['total_playtime_hours']}h",
        f"- Avg playtime: {summary['avg_playtime_hours_all_games']}h all, {summary['avg_playtime_hours_played_games']}h played-only",
        f"- Median playtime: {summary['median_playtime_hours_all_games']}h all, {summary['median_playtime_hours_played_games']}h played-only",
        f"- Last {report['recent_days']} days played games: {summary['recently_played_games']}",
        f"- Favorite games: {summary['favorite_games']}",
        f"- Rated games: {summary['rated_games']}, avg rating: {summary['avg_rating_score']}",
        "",
        "## Category Stats",
        "",
        _markdown_table(report["category_stats"], [
            "primary_category",
            "legacy_code",
            "display_name",
            "count",
            "library_percent",
            "played_count",
            "unplayed_count",
            "total_playtime_hours",
            "avg_playtime_hours",
        ]),
        "## Classification Status",
        "",
        _markdown_table(report["classification_status_stats"], ["classification_status", "count", "percent"]),
        "## Curation State",
        "",
        _markdown_table(report["curation_state_stats"], ["curation_state", "count", "percent"]),
        "## Recently Added",
        "",
        _markdown_table(report["recently_added"], ["game_id", "steam_appid", "title", "primary_category", "created_at"]),
        "## Recently Played",
        "",
        _markdown_table(report["recently_played"], ["game_id", "steam_appid", "title", "primary_category", "playtime_hours", "last_played_at"]),
        "## Top Playtime",
        "",
        _markdown_table(report["top_playtime"], ["game_id", "steam_appid", "title", "primary_category", "playtime_hours", "last_played_at"]),
        "## Pending Or Unclassified",
        "",
        _markdown_table(report["pending_or_unclassified"], ["game_id", "steam_appid", "title", "primary_category", "classification_status"]),
        "## Latest Snapshot Changes",
        "",
        f"- Snapshot count: {changes['snapshot_count']}",
        f"- Latest snapshot: {changes['latest_snapshot_id']} at {changes['latest_synced_at']} ({changes['latest_game_count']} games)",
        f"- Previous snapshot: {changes['previous_snapshot_id']}",
        "",
        "### Added Since Previous Snapshot",
        "",
        _markdown_table(changes["added_since_previous"], ["game_id", "steam_appid", "title", "playtime_hours", "last_played_at"]),
        "### Removed Since Previous Snapshot",
        "",
        _markdown_table(changes["removed_since_previous"], ["game_id", "steam_appid", "title", "playtime_hours", "last_played_at"]),
        "### Top Playtime Deltas",
        "",
        _markdown_table(changes["top_playtime_deltas"], ["game_id", "steam_appid", "title", "old_playtime_hours", "new_playtime_hours", "delta_hours"]),
    ]
    return "\n".join(lines)


def write_report_files(root: Path, report: dict[str, Any], output_dir: Path | None = None) -> dict[str, Path]:
    target = output_dir or root / "data" / "derived" / "reports"
    outputs = {
        "json": target / "library_stats.json",
        "category_stats": target / "category_stats_ext.csv",
        "recently_added": target / "recently_added.csv",
        "recently_played": target / "recently_played.csv",
        "top_playtime": target / "top_playtime.csv",
        "pending_or_unclassified": target / "pending_or_unclassified.csv",
        "snapshot_added": target / "snapshot_added_since_previous.csv",
        "snapshot_removed": target / "snapshot_removed_since_previous.csv",
        "snapshot_playtime_deltas": target / "snapshot_playtime_deltas.csv",
    }
    write_json(outputs["json"], report)
    write_csv(outputs["category_stats"], list(report["category_stats"][0].keys()) if report["category_stats"] else [], report["category_stats"])
    public_fields = [
        "game_id",
        "steam_appid",
        "title",
        "primary_category",
        "classification_status",
        "curation_state",
        "playtime_hours",
        "playtime_2weeks_hours",
        "last_played_at",
        "created_at",
        "updated_at",
        "rating_score",
        "completion_status",
    ]
    for key in ["recently_added", "recently_played", "top_playtime", "pending_or_unclassified"]:
        write_csv(outputs[key], public_fields, report[key])
    snapshot_fields = ["game_id", "steam_appid", "title", "playtime_hours", "last_played_at"]
    write_csv(outputs["snapshot_added"], snapshot_fields, report["snapshot_changes"]["added_since_previous"])
    write_csv(outputs["snapshot_removed"], snapshot_fields, report["snapshot_changes"]["removed_since_previous"])
    write_csv(outputs["snapshot_playtime_deltas"], [
        "game_id",
        "steam_appid",
        "title",
        "old_playtime_hours",
        "new_playtime_hours",
        "delta_hours",
    ], report["snapshot_changes"]["top_playtime_deltas"])
    return outputs


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Print or write comprehensive MyGameStory library statistics.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Console output format.")
    parser.add_argument("--limit", type=int, default=20, help="Rows per ranked/list section.")
    parser.add_argument("--recent-days", type=int, default=30, help="Window for recently-played counts.")
    parser.add_argument("--write-derived", action="store_true", help="Write JSON and CSV report files under data/derived/reports.")
    parser.add_argument("--output-dir", default=None, help="Custom output directory for --write-derived.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    report = build_report(root, limit=args.limit, recent_days=args.recent_days)
    if args.format == "json":
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_markdown(report))

    if args.write_derived:
        output_dir = Path(args.output_dir).resolve() if args.output_dir else None
        outputs = write_report_files(root, report, output_dir)
        print("\nWritten report files:")
        for name, path in outputs.items():
            print(f"  - {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
