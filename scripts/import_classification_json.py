from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from vaultlib import is_valid_game_id, load_taxonomies, read_jsonl, resolve_root, write_json, write_jsonl


APPDETAILS_ENDPOINT = "https://store.steampowered.com/api/appdetails"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_concatenated_json(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    pos = 0
    objects: list[dict[str, Any]] = []
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        value, end = decoder.raw_decode(text, pos)
        if not isinstance(value, dict):
            raise ValueError(f"Top-level JSON segment must be an object at offset {pos}")
        objects.append(value)
        pos = end
    return objects


def _load_csv_titles(paths: list[Path]) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                appid = row.get("appid") or row.get("steam_appid")
                if appid:
                    rows[f"steam:{appid}"] = row
    return rows


def _records_by_game_id(path: Path) -> dict[str, dict[str, Any]]:
    return {record["game_id"]: record for _, record in read_jsonl(path) if isinstance(record.get("game_id"), str)}


def _records_by_raw_name(path: Path) -> dict[str, dict[str, Any]]:
    return {record["raw_name"]: record for _, record in read_jsonl(path) if isinstance(record.get("raw_name"), str)}


def _append_unique(values: list[str], *items: Any) -> list[str]:
    for item in items:
        if item is None:
            continue
        value = str(item).strip()
        if value and value not in values:
            values.append(value)
    return values


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff" for char in value)


def _fetch_appdetails(appid: str, language: str, timeout: int) -> dict[str, Any]:
    url = f"{APPDETAILS_ENDPOINT}?{urllib.parse.urlencode({'appids': appid, 'l': language, 'filters': 'basic'})}"
    request = urllib.request.Request(url, headers={"User-Agent": "MyGameStory/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _appdetails_name(details: dict[str, Any], appid: str) -> tuple[str | None, str | None]:
    entry = details.get(appid)
    if not isinstance(entry, dict) or not entry.get("success"):
        return None, None
    data = entry.get("data")
    if not isinstance(data, dict):
        return None, None
    return data.get("name"), data.get("type")


def _load_or_fetch_appdetails(
    root: Path,
    appids: list[str],
    timestamp: str,
    fetch: bool,
    timeout: int,
    delay: float,
) -> tuple[dict[str, dict[str, Any]], Path | None]:
    if not fetch or not appids:
        return {}, None
    responses: dict[str, dict[str, Any]] = {}
    for appid in appids:
        responses[appid] = {}
        for language in ("english", "schinese"):
            try:
                responses[appid][language] = _fetch_appdetails(appid, language, timeout)
            except Exception as exc:  # noqa: BLE001 - captured in raw import metadata.
                responses[appid][language] = {"error": str(exc)}
            time.sleep(delay)
    output = root / "data" / "imports" / "steam_appdetails" / f"{timestamp}.classification_json_appdetails.json"
    write_json(output, {
        "source": "steam_appdetails",
        "timestamp": timestamp,
        "appids": appids,
        "responses": responses,
        "created_at": _now_iso(),
    })
    return responses, output


def _parse_entries(objects: list[dict[str, Any]], taxonomy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    primary_categories = taxonomy["primary_categories"]
    for segment_index, obj in enumerate(objects, start=1):
        for category_key, payload in obj.items():
            if category_key not in primary_categories:
                invalid.append({
                    "segment_index": segment_index,
                    "category_key": category_key,
                    "reason": "unknown_primary_category",
                })
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
                invalid.append({
                    "segment_index": segment_index,
                    "category_key": category_key,
                    "reason": "category_payload_missing_items",
                })
                continue
            collection_title = payload.get("ocr_collection")
            for raw_name, game_id in payload["items"].items():
                if not isinstance(game_id, str) or not is_valid_game_id(game_id):
                    invalid.append({
                        "segment_index": segment_index,
                        "category_key": category_key,
                        "raw_name": raw_name,
                        "game_id": game_id,
                        "reason": "invalid_game_id",
                    })
                    continue
                entries.append({
                    "segment_index": segment_index,
                    "category_key": category_key,
                    "collection_title": collection_title,
                    "raw_name": str(raw_name),
                    "game_id": game_id,
                    "steam_appid": int(game_id.split(":", 1)[1]) if game_id.startswith("steam:") else None,
                })
    return entries, invalid


def _game_from_sources(
    game_id: str,
    raw_name: str,
    csv_titles: dict[str, dict[str, str]],
    appdetails: dict[str, dict[str, Any]],
    now: str,
    source_name: str,
) -> dict[str, Any]:
    steam_appid = int(game_id.split(":", 1)[1])
    csv_row = csv_titles.get(game_id, {})
    csv_name = csv_row.get("name") or csv_row.get("steam_name")
    appid = str(steam_appid)
    en_name, en_type = _appdetails_name(appdetails.get(appid, {}).get("english", {}), appid)
    zh_name, zh_type = _appdetails_name(appdetails.get(appid, {}).get("schinese", {}), appid)
    canonical = csv_name or en_name or zh_name or raw_name
    zh_cn = zh_name if zh_name and zh_name != canonical else (raw_name if _contains_cjk(raw_name) and raw_name != canonical else None)
    title_en = en_name or (csv_name if csv_name and not _contains_cjk(csv_name) else canonical)
    aliases = _append_unique([], raw_name, csv_name, en_name, zh_name)
    return {
        "game_id": game_id,
        "platform": "steam",
        "steam_appid": steam_appid,
        "type": en_type or zh_type or "game",
        "titles": {
            "canonical": canonical,
            "zh_cn": zh_cn,
            "en": title_en,
        },
        "aliases": aliases,
        "identity_source": {
            "source": source_name,
            "snapshot_id": None,
        },
        "created_at": now,
        "updated_at": now,
    }


def _merge_existing_game_aliases(existing: dict[str, Any], raw_names: list[str], now: str, source_name: str) -> dict[str, Any]:
    merged = dict(existing)
    aliases = list(merged.get("aliases") or [])
    _append_unique(aliases, *raw_names)
    merged["aliases"] = aliases
    merged["updated_at"] = now
    merged.setdefault("identity_source", {"source": source_name, "snapshot_id": None})
    return merged


def build_preview(
    root: Path,
    input_path: Path,
    timestamp: str,
    fetch_appdetails: bool,
    timeout: int,
    delay: float,
) -> dict[str, Path]:
    taxonomy = load_taxonomies(root)
    objects = _read_concatenated_json(input_path)
    entries, invalid_records = _parse_entries(objects, taxonomy)
    now = _now_iso()
    source_name = "classification_json_import"

    games_by_id = _records_by_game_id(root / "data" / "manual" / "games.jsonl")
    aliases_by_id = _records_by_game_id(root / "data" / "manual" / "aliases.jsonl")
    classifications_by_id = _records_by_game_id(root / "data" / "manual" / "classifications.jsonl")
    name_resolution_by_raw = _records_by_raw_name(root / "data" / "manual" / "name_resolution.jsonl")
    csv_titles = _load_csv_titles([
        root / "data" / "imports" / "steam_owned" / "steam_library_raw.csv",
        root / "data" / "imports" / "steam_owned" / "legacy" / "steam_library_raw.csv",
    ])

    entries_by_game: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        entries_by_game.setdefault(entry["game_id"], []).append(entry)

    missing_appids = sorted({
        str(entry["steam_appid"])
        for entry in entries
        if entry["game_id"] not in games_by_id and entry["steam_appid"] is not None
    }, key=int)
    appdetails, appdetails_output = _load_or_fetch_appdetails(
        root, missing_appids, timestamp, fetch_appdetails, timeout, delay
    )

    games_preview: list[dict[str, Any]] = []
    aliases_preview: list[dict[str, Any]] = []
    name_resolution_preview: list[dict[str, Any]] = []
    classifications_preview: list[dict[str, Any]] = []
    pending_preview: list[dict[str, Any]] = []

    for game_id, grouped_entries in sorted(entries_by_game.items(), key=lambda item: item[0]):
        raw_names = [entry["raw_name"] for entry in grouped_entries]
        categories = sorted({entry["category_key"] for entry in grouped_entries})
        existing_game = games_by_id.get(game_id)
        if existing_game:
            games_preview.append(_merge_existing_game_aliases(existing_game, raw_names, now, source_name))
        else:
            games_preview.append(_game_from_sources(game_id, raw_names[0], csv_titles, appdetails, now, source_name))

        alias_record = dict(aliases_by_id.get(game_id, {"game_id": game_id, "aliases": [], "source": source_name}))
        aliases = list(alias_record.get("aliases") or [])
        _append_unique(aliases, *raw_names)
        game_preview = games_preview[-1]
        titles = game_preview.get("titles") or {}
        _append_unique(aliases, titles.get("canonical"), titles.get("zh_cn"), titles.get("en"), *(game_preview.get("aliases") or []))
        alias_record.update({"aliases": aliases, "source": source_name, "updated_at": now})
        aliases_preview.append(alias_record)

        for entry in grouped_entries:
            current_resolution = dict(name_resolution_by_raw.get(entry["raw_name"], {}))
            current_resolution.update({
                "raw_name": entry["raw_name"],
                "game_id": game_id,
                "match_method": "raw_json_appid",
                "confidence": 1.0,
                "candidates": [],
                "source_file": str(input_path),
                "resolved_at": now,
                "status": "resolved",
                "notes": "Resolved by explicit appid from classification JSON source.",
            })
            name_resolution_preview.append(current_resolution)

        existing_classification = classifications_by_id.get(game_id)
        if len(categories) > 1:
            pending_preview.append({
                "game_id": game_id,
                "reason": "multiple_categories_in_same_classification_json_source",
                "candidate_categories": categories,
                "raw_names": raw_names,
                "source_file": str(input_path),
                "created_at": now,
                "status": "pending_review",
            })
            continue
        category = categories[0]
        existing_category = existing_classification.get("primary_category") if existing_classification else None
        if existing_category and existing_category != category:
            pending_preview.append({
                "game_id": game_id,
                "reason": "primary_category_conflict",
                "current_primary_category": existing_category,
                "suggested_primary_category": category,
                "raw_names": raw_names,
                "source_file": str(input_path),
                "created_at": now,
                "status": "pending_review",
            })
            continue
        classification = dict(existing_classification or {})
        evidence = list(classification.get("classification_evidence") or [])
        for entry in grouped_entries:
            evidence.append({
                "type": "classification_json_appid",
                "source_file": str(input_path),
                "collection_title": entry.get("collection_title"),
                "raw_name": entry["raw_name"],
                "match_method": "raw_json_appid",
                "confidence": 1.0,
            })
        classification.update({
            "game_id": game_id,
            "primary_category": category,
            "classification_status": "confirmed",
            "favorite": bool(classification.get("favorite", False)),
            "special_flags": list(classification.get("special_flags") or []),
            "manual_tags": list(classification.get("manual_tags") or []),
            "classification_evidence": evidence,
            "updated_at": now,
        })
        classification.setdefault("curation_state", None)
        classification.setdefault("platform_plan", None)
        classification.setdefault("play_plan", None)
        classifications_preview.append(classification)

    output_stem = timestamp
    parse_output = root / "data" / "imports" / "ocr" / f"{output_stem}.classification_json_parse.json"
    outputs = {
        "parse": parse_output,
        "games_preview": root / "data" / "manual" / f"games.preview.{output_stem}.jsonl",
        "aliases_preview": root / "data" / "manual" / f"aliases.preview.{output_stem}.jsonl",
        "name_resolution_preview": root / "data" / "manual" / f"name_resolution.preview.{output_stem}.jsonl",
        "classification_preview": root / "data" / "manual" / f"classifications.preview.{output_stem}.jsonl",
        "pending_preview": root / "data" / "suggestions" / f"pending_review.preview.{output_stem}.jsonl",
    }
    write_json(parse_output, {
        "source": "classification_json_import",
        "input_path": str(input_path),
        "timestamp": timestamp,
        "created_at": now,
        "json_segments": len(objects),
        "entry_count": len(entries),
        "unique_game_count": len(entries_by_game),
        "invalid_records": invalid_records,
        "missing_game_count": len(missing_appids),
        "appdetails_output": str(appdetails_output) if appdetails_output else None,
        "preview_counts": {
            "games": len(games_preview),
            "aliases": len(aliases_preview),
            "name_resolution": len(name_resolution_preview),
            "classifications": len(classifications_preview),
            "pending": len(pending_preview),
        },
    })
    write_jsonl(outputs["games_preview"], games_preview)
    write_jsonl(outputs["aliases_preview"], aliases_preview)
    write_jsonl(outputs["name_resolution_preview"], name_resolution_preview)
    write_jsonl(outputs["classification_preview"], classifications_preview)
    write_jsonl(outputs["pending_preview"], pending_preview)
    return outputs


def _upsert_by_game_id(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    merged: dict[str, dict[str, Any]] = {}
    for record in existing:
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            continue
        if game_id not in merged:
            order.append(game_id)
        merged[game_id] = record
    for record in incoming:
        game_id = record.get("game_id")
        if not isinstance(game_id, str):
            continue
        if game_id not in merged:
            order.append(game_id)
        base = dict(merged.get(game_id, {}))
        base.update(record)
        merged[game_id] = base
    return [merged[game_id] for game_id in order]


def _upsert_by_raw_name(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    merged: dict[str, dict[str, Any]] = {}
    for record in existing:
        raw_name = record.get("raw_name")
        if not isinstance(raw_name, str):
            continue
        if raw_name not in merged:
            order.append(raw_name)
        merged[raw_name] = record
    for record in incoming:
        raw_name = record.get("raw_name")
        if not isinstance(raw_name, str):
            continue
        if raw_name not in merged:
            order.append(raw_name)
        base = dict(merged.get(raw_name, {}))
        base.update(record)
        merged[raw_name] = base
    return [merged[raw_name] for raw_name in order]


def apply_preview(root: Path, timestamp: str) -> None:
    targets = [
        ("games", root / "data" / "manual" / "games.jsonl", root / "data" / "manual" / f"games.preview.{timestamp}.jsonl", _upsert_by_game_id),
        ("aliases", root / "data" / "manual" / "aliases.jsonl", root / "data" / "manual" / f"aliases.preview.{timestamp}.jsonl", _upsert_by_game_id),
        ("name_resolution", root / "data" / "manual" / "name_resolution.jsonl", root / "data" / "manual" / f"name_resolution.preview.{timestamp}.jsonl", _upsert_by_raw_name),
        ("classifications", root / "data" / "manual" / "classifications.jsonl", root / "data" / "manual" / f"classifications.preview.{timestamp}.jsonl", _upsert_by_game_id),
    ]
    for _, target, preview, merge in targets:
        if not preview.exists():
            raise FileNotFoundError(preview)
        existing = [record for _, record in read_jsonl(target)]
        incoming = [record for _, record in read_jsonl(preview)]
        write_jsonl(target, merge(existing, incoming))

    pending_preview = root / "data" / "suggestions" / f"pending_review.preview.{timestamp}.jsonl"
    if pending_preview.exists():
        pending_target = root / "data" / "suggestions" / "pending_review.jsonl"
        pending_rows = [record for _, record in read_jsonl(pending_preview)]
        if pending_rows:
            write_jsonl(pending_target, [record for _, record in read_jsonl(pending_target)] + pending_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import category JSON with explicit appid mappings through reviewable previews.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the MyGameStory repository root.")
    parser.add_argument("--input", default=None, help="Classification JSON file. Supports concatenated top-level JSON objects.")
    parser.add_argument("--timestamp", required=True, help="Timestamp/batch id used in preview filenames.")
    parser.add_argument("--fetch-appdetails", action="store_true", help="Fetch Steam appdetails for appids missing from games/csv.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout for appdetails fetch.")
    parser.add_argument("--delay", type=float, default=0.12, help="Delay between appdetails requests.")
    parser.add_argument("--apply-preview", action="store_true", help="Apply previews for the given timestamp.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    if args.apply_preview:
        apply_preview(root, args.timestamp)
        print(f"Applied classification JSON previews: {args.timestamp}")
        return 0
    if not args.input:
        parser.error("--input is required unless --apply-preview is used")
    outputs = build_preview(
        root=root,
        input_path=Path(args.input).resolve(),
        timestamp=args.timestamp,
        fetch_appdetails=args.fetch_appdetails,
        timeout=args.timeout,
        delay=args.delay,
    )
    for name, path in outputs.items():
        print(f"Generated {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
