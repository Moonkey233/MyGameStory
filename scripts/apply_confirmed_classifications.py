from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from vaultlib import load_taxonomies, now_iso, read_jsonl, resolve_root, upsert_by_game_id, write_jsonl


ALLOWED_FIELDS = {
    "game_id",
    "primary_category",
    "classification_status",
    "curation_state",
    "favorite",
    "special_flags",
    "manual_tags",
    "platform_plan",
    "play_plan",
    "classification_evidence",
    "updated_at",
}


def _normalize_record(record: dict[str, Any], taxonomies: dict[str, Any], updated_at: str) -> dict[str, Any]:
    cleaned = {key: value for key, value in record.items() if key in ALLOWED_FIELDS}
    if "game_id" not in cleaned:
        raise ValueError("confirmed classification record missing game_id")
    primary_category = cleaned.get("primary_category")
    if primary_category is not None and primary_category not in taxonomies["primary_categories"]:
        raise ValueError(f"unknown primary_category: {primary_category}")
    curation_state = cleaned.get("curation_state")
    if curation_state is not None and curation_state not in taxonomies["curation_states"]:
        raise ValueError(f"unknown curation_state: {curation_state}")
    for flag in cleaned.get("special_flags") or []:
        if flag not in taxonomies["special_flags"]:
            raise ValueError(f"unknown special_flag: {flag}")
    cleaned.setdefault("classification_status", "confirmed")
    cleaned.setdefault("favorite", False)
    cleaned.setdefault("special_flags", [])
    cleaned.setdefault("manual_tags", [])
    cleaned.setdefault("classification_evidence", [])
    cleaned["updated_at"] = updated_at
    return cleaned


def build_preview(root: Path, input_path: Path, timestamp: str) -> Path:
    taxonomies = load_taxonomies(root)
    updated_at = now_iso()
    records = [_normalize_record(record, taxonomies, updated_at) for _, record in read_jsonl(input_path)]
    preview_path = root / "data" / "manual" / f"classifications.preview.{timestamp}.jsonl"
    if preview_path.exists():
        raise FileExistsError(f"Preview already exists: {preview_path}")
    write_jsonl(preview_path, records)
    return preview_path


def apply_preview(root: Path, preview_path: Path) -> None:
    classifications_path = root / "data" / "manual" / "classifications.jsonl"
    existing = [record for _, record in read_jsonl(classifications_path)]
    incoming = [record for _, record in read_jsonl(preview_path)]
    merged = upsert_by_game_id(existing, incoming)
    write_jsonl(classifications_path, merged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview and apply manually confirmed classifications.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the MyGameStory repository root.")
    parser.add_argument("--input", default=None, help="Confirmed classification JSONL input.")
    parser.add_argument("--timestamp", default=None, help="Timestamp used in preview filename.")
    parser.add_argument("--apply-preview", default=None, help="Apply a previously reviewed preview JSONL.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    if args.apply_preview:
        apply_preview(root, Path(args.apply_preview))
        print(f"Applied preview: {args.apply_preview}")
        return 0

    if not args.input or not args.timestamp:
        parser.error("--input and --timestamp are required unless --apply-preview is used")
    preview = build_preview(root, Path(args.input), args.timestamp)
    print(f"Preview written: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
