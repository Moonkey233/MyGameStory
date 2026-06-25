from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from vaultlib import load_taxonomies, now_iso, resolve_game, resolve_root, write_json, write_jsonl


COLLECTION_CODE_RE = re.compile(r"\b([AB]\.\d{2})\b")


def _ocr_with_optional_tesseract(image_path: Path) -> tuple[str, list[dict[str, Any]], str | None]:
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:  # noqa: BLE001 - optional dependency fallback.
        return "", [], f"OCR dependencies are not installed: {exc}"

    image = Image.open(image_path)
    text = pytesseract.image_to_string(image, lang="chi_sim+eng")
    lines = [{"text": line.strip(), "confidence": None} for line in text.splitlines() if line.strip()]
    return text, lines, None


def _load_text_override(path: Path) -> tuple[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    lines = [{"text": line.strip(), "confidence": None} for line in text.splitlines() if line.strip()]
    return text, lines


def _parse_collections(lines: list[dict[str, Any]], taxonomies: dict[str, Any]) -> list[dict[str, Any]]:
    collections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    all_legacy_codes = set(taxonomies["legacy_to_primary"]) | set(taxonomies["legacy_to_curation"]) | set(taxonomies["legacy_to_flag"])

    for line in lines:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        match = COLLECTION_CODE_RE.search(text)
        if match and match.group(1) in all_legacy_codes:
            current = {
                "raw_title": text,
                "legacy_code": match.group(1),
                "items": [],
            }
            collections.append(current)
            continue
        if current is not None:
            current["items"].append({
                "raw_name": text,
                "confidence": line.get("confidence"),
            })
    return collections


def build_ocr_preview(root: Path, image_path: Path, timestamp: str, text_override: Path | None = None) -> dict[str, Path]:
    taxonomies = load_taxonomies(root)
    created_at = now_iso()
    if text_override:
        raw_text, ocr_lines = _load_text_override(text_override)
        error = None
        ocr_engine = "text_override"
    else:
        raw_text, ocr_lines, error = _ocr_with_optional_tesseract(image_path)
        ocr_engine = "pytesseract_optional"

    collections = _parse_collections(ocr_lines, taxonomies)
    suggestions: list[dict[str, Any]] = []
    pending_review: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for collection in collections:
        legacy_code = collection["legacy_code"]
        primary_category = taxonomies["legacy_to_primary"].get(legacy_code)
        curation_state = taxonomies["legacy_to_curation"].get(legacy_code)
        special_flag = taxonomies["legacy_to_flag"].get(legacy_code)

        for item in collection["items"]:
            resolved = resolve_game(root, raw_name=item["raw_name"], source_file=str(image_path))
            if resolved["status"] != "resolved":
                unresolved.append({
                    **resolved,
                    "ocr_collection": collection["raw_title"],
                    "ocr_confidence": item.get("confidence"),
                })
                continue
            if primary_category:
                suggestions.append({
                    "game_id": resolved["game_id"],
                    "suggested_primary_category": primary_category["key"],
                    "candidate_categories": [primary_category["key"]],
                    "confidence": min(float(resolved["confidence"]), 0.85),
                    "evidence": [{
                        "type": "screenshot_ocr_collection",
                        "source_file": str(image_path),
                        "collection_title": collection["raw_title"],
                        "raw_name": item["raw_name"],
                    }],
                    "source": "screenshot_ocr",
                    "created_at": created_at,
                    "status": "pending_review",
                })
            else:
                pending_review.append({
                    "game_id": resolved["game_id"],
                    "reason": "screenshot_ocr_non_primary_collection",
                    "suggested_curation_state": curation_state["key"] if curation_state else None,
                    "suggested_special_flag": special_flag["key"] if special_flag else None,
                    "evidence": [{
                        "type": "screenshot_ocr_collection",
                        "source_file": str(image_path),
                        "collection_title": collection["raw_title"],
                        "raw_name": item["raw_name"],
                    }],
                    "source": "screenshot_ocr",
                    "created_at": created_at,
                    "status": "pending_review",
                })

    ocr_output = root / "data" / "imports" / "ocr" / f"{timestamp}.ocr.json"
    suggestions_output = root / "data" / "suggestions" / f"classification_suggestions.preview.{timestamp}.jsonl"
    pending_output = root / "data" / "suggestions" / f"pending_review.preview.{timestamp}.jsonl"
    unresolved_output = root / "data" / "suggestions" / f"unresolved_names.preview.{timestamp}.jsonl"

    write_json(ocr_output, {
        "source": "screenshot_ocr",
        "image_path": str(image_path),
        "timestamp": timestamp,
        "created_at": created_at,
        "ocr_engine": ocr_engine,
        "error": error,
        "raw_text": raw_text,
        "lines": ocr_lines,
        "parsed_collections": collections,
    })
    write_jsonl(suggestions_output, suggestions)
    write_jsonl(pending_output, pending_review)
    write_jsonl(unresolved_output, unresolved)
    return {
        "ocr": ocr_output,
        "classification_suggestions_preview": suggestions_output,
        "pending_review_preview": pending_output,
        "unresolved_names_preview": unresolved_output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create OCR-based classification suggestion previews from one screenshot source.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the game-vault directory.")
    parser.add_argument("--image", required=True, help="Screenshot image path.")
    parser.add_argument("--timestamp", required=True, help="Timestamp used in output filenames.")
    parser.add_argument("--text-override", default=None, help="Optional UTF-8 text file to use instead of OCR, useful for testing/manual OCR.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    outputs = build_ocr_preview(
        root=root,
        image_path=Path(args.image).resolve(),
        timestamp=args.timestamp,
        text_override=Path(args.text_override).resolve() if args.text_override else None,
    )
    for name, path in outputs.items():
        print(f"Generated {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

