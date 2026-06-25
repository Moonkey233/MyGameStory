from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from vaultlib import load_taxonomies, now_iso, read_json, read_jsonl, resolve_game, resolve_root, write_json, write_jsonl


COLLECTION_CODE_RE = re.compile(r"\b([AB]\.\d{2})\b")


def _parse_text(path: Path, taxonomies: dict[str, Any]) -> list[dict[str, Any]]:
    all_codes = set(taxonomies["legacy_to_primary"]) | set(taxonomies["legacy_to_curation"]) | set(taxonomies["legacy_to_flag"])
    collections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = raw_line.strip()
        if not text:
            continue
        match = COLLECTION_CODE_RE.search(text)
        if match and match.group(1) in all_codes:
            current = {
                "raw_title": text,
                "legacy_code": match.group(1),
                "line_no": line_no,
                "items": [],
            }
            collections.append(current)
            continue
        if current is not None:
            current["items"].append({"raw_name": text, "line_no": line_no})
    return collections


def _load_alias_map(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Alias map must be a JSON object: {path}")
    return payload


def _resolve_with_alias_map(root: Path, raw_name: str, alias_map: dict[str, Any], source_file: str) -> dict[str, Any]:
    mapped = alias_map.get(raw_name)
    if isinstance(mapped, str):
        if mapped.startswith("steam:"):
            return resolve_game(root, raw_name=raw_name, game_id=mapped, source_file=source_file)
        if mapped.isdigit():
            return resolve_game(root, raw_name=raw_name, steam_appid=mapped, source_file=source_file)
        resolved = resolve_game(root, raw_name=mapped, source_file=source_file)
        if resolved["status"] == "resolved":
            resolved["raw_name"] = raw_name
            resolved["match_method"] = f"alias_map:{resolved['match_method']}"
        return resolved
    if isinstance(mapped, dict):
        return resolve_game(
            root,
            raw_name=raw_name,
            steam_appid=mapped.get("steam_appid"),
            game_id=mapped.get("game_id"),
            source_file=source_file,
        )
    return resolve_game(root, raw_name=raw_name, source_file=source_file)


def _name_hints(raw_name: str) -> list[str]:
    hints: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip(" \t\r\n-—–_/／|:：,，.。'\"“”‘’《》")
        if cleaned and cleaned != raw_name and cleaned not in hints:
            hints.append(cleaned)

    for segment in re.split(r"[/／|]", raw_name):
        add(segment)

    for match in re.finditer(r"[\(（]([^\)）]+)[\)）]", raw_name):
        add(match.group(1))

    ascii_runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9\s:'’™®.&+!-]*[A-Za-z0-9™®!)]?", raw_name)
    for run in ascii_runs:
        add(run)

    return hints


def _resolve_name(root: Path, raw_name: str, alias_map: dict[str, Any], source_file: str) -> dict[str, Any]:
    resolved = _resolve_with_alias_map(root, raw_name, alias_map, source_file)
    if resolved["status"] == "resolved":
        return resolved
    candidates = [resolved] if resolved.get("candidates") else []
    for hint in _name_hints(raw_name):
        hint_resolved = resolve_game(root, raw_name=hint, source_file=source_file)
        if hint_resolved["status"] == "resolved":
            hint_resolved["raw_name"] = raw_name
            hint_resolved["match_method"] = f"hint:{hint}:{hint_resolved['match_method']}"
            return hint_resolved
        if hint_resolved.get("candidates"):
            candidates.append(hint_resolved)
    if candidates:
        best = max(candidates, key=lambda item: float(item.get("confidence") or 0))
        best["raw_name"] = raw_name
        return best
    return resolved


def _existing_classifications(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "manual" / "classifications.jsonl"
    return {record["game_id"]: record for _, record in read_jsonl(path) if isinstance(record.get("game_id"), str)}


def _classification_record(
    game_id: str,
    primary_category: str,
    source_file: str,
    collection_title: str,
    raw_name: str,
    resolved: dict[str, Any],
    updated_at: str,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(existing or {})
    evidence = list(base.get("classification_evidence") or [])
    evidence.append({
        "type": "ocr_text_collection",
        "source_file": source_file,
        "collection_title": collection_title,
        "raw_name": raw_name,
        "match_method": resolved.get("match_method"),
        "confidence": resolved.get("confidence"),
    })
    base.update({
        "game_id": game_id,
        "primary_category": primary_category,
        "classification_status": "confirmed",
        "favorite": bool(base.get("favorite", False)),
        "special_flags": list(base.get("special_flags") or []),
        "manual_tags": list(base.get("manual_tags") or []),
        "classification_evidence": evidence,
        "updated_at": updated_at,
    })
    base.setdefault("curation_state", None)
    base.setdefault("platform_plan", None)
    base.setdefault("play_plan", None)
    return base


def build_preview(root: Path, input_path: Path, timestamp: str, alias_map_path: Path | None = None) -> dict[str, Path]:
    taxonomies = load_taxonomies(root)
    alias_map = _load_alias_map(alias_map_path)
    updated_at = now_iso()
    collections = _parse_text(input_path, taxonomies)
    existing = _existing_classifications(root)

    preview_records: list[dict[str, Any]] = []
    unresolved_records: list[dict[str, Any]] = []
    pending_records: list[dict[str, Any]] = []
    seen_preview_game_ids: set[str] = set()

    for collection in collections:
        category = taxonomies["legacy_to_primary"].get(collection["legacy_code"])
        if not category:
            pending_records.append({
                "reason": "non_primary_collection_not_supported_by_classification_text_import",
                "collection_title": collection["raw_title"],
                "source_file": str(input_path),
                "created_at": updated_at,
                "status": "pending_review",
            })
            continue

        for item in collection["items"]:
            raw_name = item["raw_name"]
            resolved = _resolve_name(root, raw_name, alias_map, str(input_path))
            if resolved["status"] != "resolved" or not resolved.get("game_id"):
                unresolved_records.append({
                    **resolved,
                    "ocr_collection": collection["raw_title"],
                    "line_no": item["line_no"],
                })
                continue

            game_id = str(resolved["game_id"])
            current = existing.get(game_id)
            current_category = current.get("primary_category") if current else None
            if current_category and current_category != category["key"]:
                pending_records.append({
                    "game_id": game_id,
                    "reason": "primary_category_conflict",
                    "current_primary_category": current_category,
                    "suggested_primary_category": category["key"],
                    "raw_name": raw_name,
                    "source_file": str(input_path),
                    "created_at": updated_at,
                    "status": "pending_review",
                })
                continue
            if game_id in seen_preview_game_ids:
                pending_records.append({
                    "game_id": game_id,
                    "reason": "duplicate_game_in_same_classification_text_source",
                    "suggested_primary_category": category["key"],
                    "raw_name": raw_name,
                    "source_file": str(input_path),
                    "created_at": updated_at,
                    "status": "pending_review",
                })
                continue

            preview_records.append(_classification_record(
                game_id=game_id,
                primary_category=category["key"],
                source_file=str(input_path),
                collection_title=collection["raw_title"],
                raw_name=raw_name,
                resolved=resolved,
                updated_at=updated_at,
                existing=current,
            ))
            seen_preview_game_ids.add(game_id)

    parse_output = root / "data" / "imports" / "ocr" / f"{timestamp}.classification_text_parse.json"
    preview_output = root / "data" / "manual" / f"classifications.preview.{timestamp}.jsonl"
    unresolved_output = root / "data" / "suggestions" / f"unresolved_names.preview.{timestamp}.jsonl"
    pending_output = root / "data" / "suggestions" / f"pending_review.preview.{timestamp}.jsonl"

    write_json(parse_output, {
        "source": "classification_text",
        "input_path": str(input_path),
        "alias_map_path": str(alias_map_path) if alias_map_path else None,
        "timestamp": timestamp,
        "created_at": updated_at,
        "parsed_collections": collections,
        "preview_count": len(preview_records),
        "unresolved_count": len(unresolved_records),
        "pending_count": len(pending_records),
    })
    write_jsonl(preview_output, preview_records)
    write_jsonl(unresolved_output, unresolved_records)
    write_jsonl(pending_output, pending_records)
    return {
        "parse": parse_output,
        "classification_preview": preview_output,
        "unresolved_preview": unresolved_output,
        "pending_preview": pending_output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build confirmed-classification previews from plain OCR collection text.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--input", required=True, help="UTF-8 OCR/classification text file.")
    parser.add_argument("--timestamp", required=True, help="Timestamp used in output filenames.")
    parser.add_argument("--alias-map", default=None, help="Optional JSON object mapping raw OCR names to game_id/appid/title.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    outputs = build_preview(
        root=root,
        input_path=Path(args.input).resolve(),
        timestamp=args.timestamp,
        alias_map_path=Path(args.alias_map).resolve() if args.alias_map else None,
    )
    for name, path in outputs.items():
        print(f"Generated {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
