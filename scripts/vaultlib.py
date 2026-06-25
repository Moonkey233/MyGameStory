from __future__ import annotations

import csv
import difflib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


GAME_ID_RE = re.compile(r"^[a-z][a-z0-9_+-]*:[A-Za-z0-9_.-]+$")
STEAM_GAME_ID_RE = re.compile(r"^steam:\d+$")
TRADEMARK_CHARS = "™®©℠"
COMMON_SYMBOL_RE = re.compile(r"[\s\-_·:：;；,，.。!！?？'\"“”‘’`~／/\\|]+")
BRACKET_REPLACEMENTS = str.maketrans({
    "（": "(",
    "）": ")",
    "【": "(",
    "】": ")",
    "［": "(",
    "］": ")",
    "〔": "(",
    "〕": ")",
    "〈": "(",
    "〉": ")",
    "《": "(",
    "》": ")",
})


class JsonlError(ValueError):
    def __init__(self, path: Path, line_no: int, message: str) -> None:
        super().__init__(f"{path}:{line_no}: {message}")
        self.path = path
        self.line_no = line_no
        self.message = message


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_root(root: str | Path | None = None) -> Path:
    return Path(root).resolve() if root else default_root()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    records: list[tuple[int, dict[str, Any]]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JsonlError(path, line_no, exc.msg) from exc
            if not isinstance(value, dict):
                raise JsonlError(path, line_no, "JSONL record must be an object")
            records.append((line_no, value))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]], append: bool = False) -> None:
    ensure_parent(path)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def load_taxonomies(root: Path) -> dict[str, Any]:
    data = read_json(root / "config" / "taxonomies.json")
    primary = {item["key"]: item for item in data.get("primary_categories", [])}
    curation = {item["key"]: item for item in data.get("curation_states", [])}
    flags = {item["key"]: item for item in data.get("special_flags", [])}
    legacy_to_primary = {item["legacy_code"]: item for item in data.get("primary_categories", [])}
    legacy_to_curation = {item["legacy_code"]: item for item in data.get("curation_states", [])}
    legacy_to_flag = {item["legacy_code"]: item for item in data.get("special_flags", [])}
    return {
        "raw": data,
        "primary_categories": primary,
        "curation_states": curation,
        "special_flags": flags,
        "legacy_to_primary": legacy_to_primary,
        "legacy_to_curation": legacy_to_curation,
        "legacy_to_flag": legacy_to_flag,
    }


def is_valid_game_id(game_id: Any) -> bool:
    if not isinstance(game_id, str) or not GAME_ID_RE.match(game_id):
        return False
    if game_id.startswith("steam:"):
        return bool(STEAM_GAME_ID_RE.match(game_id))
    return True


def coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.translate(BRACKET_REPLACEMENTS)
    for char in TRADEMARK_CHARS:
        normalized = normalized.replace(char, "")
    normalized = normalized.casefold()
    normalized = COMMON_SYMBOL_RE.sub("", normalized)
    return normalized.strip()


def iter_name_values(game: dict[str, Any]) -> list[str]:
    values: list[str] = []
    titles = game.get("titles") or {}
    if isinstance(titles, dict):
        for key in ("canonical", "zh_cn", "en"):
            value = titles.get(key)
            if value:
                values.append(str(value))
    aliases = game.get("aliases") or []
    if isinstance(aliases, list):
        values.extend(str(value) for value in aliases if value)
    return values


def load_games(root: Path) -> list[dict[str, Any]]:
    return [record for _, record in read_jsonl(root / "data" / "manual" / "games.jsonl")]


def load_games_by_id(root: Path) -> dict[str, dict[str, Any]]:
    return {game["game_id"]: game for game in load_games(root) if "game_id" in game}


def title_display(game: dict[str, Any] | None) -> str:
    if not game:
        return ""
    titles = game.get("titles") or {}
    if not isinstance(titles, dict):
        return ""
    return str(titles.get("zh_cn") or titles.get("canonical") or titles.get("en") or "")


def build_alias_indexes(root: Path) -> dict[str, Any]:
    games = load_games(root)
    by_id = {game.get("game_id"): game for game in games if game.get("game_id")}
    exact: dict[str, set[str]] = {}
    normalized: dict[str, set[str]] = {}
    appids: dict[str, str] = {}

    def add_name(game_id: str, name: str) -> None:
        exact.setdefault(name.strip(), set()).add(game_id)
        norm = normalize_name(name)
        if norm:
            normalized.setdefault(norm, set()).add(game_id)

    for game in games:
        game_id = game.get("game_id")
        if not isinstance(game_id, str):
            continue
        steam_appid = game.get("steam_appid")
        if steam_appid is not None:
            appids[str(steam_appid)] = game_id
        for name in iter_name_values(game):
            add_name(game_id, name)

    aliases_path = root / "data" / "manual" / "aliases.jsonl"
    for _, alias_record in read_jsonl(aliases_path):
        game_id = alias_record.get("game_id")
        if not isinstance(game_id, str):
            continue
        for alias in alias_record.get("aliases") or []:
            if alias:
                add_name(game_id, str(alias))

    return {
        "by_id": by_id,
        "exact": exact,
        "normalized": normalized,
        "appids": appids,
    }


def _candidate(game_id: str, games_by_id: dict[str, dict[str, Any]], score: float, method: str) -> dict[str, Any]:
    return {
        "game_id": game_id,
        "title": title_display(games_by_id.get(game_id)),
        "score": round(score, 4),
        "match_method": method,
    }


def resolve_game(
    root: Path,
    raw_name: str | None = None,
    steam_appid: int | str | None = None,
    game_id: str | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    indexes = build_alias_indexes(root)
    by_id: dict[str, dict[str, Any]] = indexes["by_id"]
    result: dict[str, Any] = {
        "raw_name": raw_name,
        "game_id": None,
        "match_method": None,
        "confidence": 0.0,
        "candidates": [],
        "source_file": source_file,
        "resolved_at": now_iso(),
        "status": "unresolved",
    }

    if steam_appid is not None and str(steam_appid) in indexes["appids"]:
        matched_id = indexes["appids"][str(steam_appid)]
        result.update({
            "game_id": matched_id,
            "match_method": "steam_appid_exact",
            "confidence": 1.0,
            "status": "resolved",
        })
        return result

    if game_id and game_id in by_id:
        result.update({
            "game_id": game_id,
            "match_method": "game_id_exact",
            "confidence": 1.0,
            "status": "resolved",
        })
        return result

    if not raw_name:
        return result

    exact_matches = indexes["exact"].get(raw_name.strip(), set())
    if len(exact_matches) == 1:
        matched_id = next(iter(exact_matches))
        result.update({
            "game_id": matched_id,
            "match_method": "title_or_alias_exact",
            "confidence": 1.0,
            "status": "resolved",
        })
        return result
    if len(exact_matches) > 1:
        result["status"] = "ambiguous"
        result["match_method"] = "title_or_alias_exact"
        result["candidates"] = [_candidate(item, by_id, 1.0, "title_or_alias_exact") for item in sorted(exact_matches)]
        return result

    normalized_name = normalize_name(raw_name)
    norm_matches = indexes["normalized"].get(normalized_name, set())
    if len(norm_matches) == 1:
        matched_id = next(iter(norm_matches))
        result.update({
            "game_id": matched_id,
            "match_method": "normalized_exact",
            "confidence": 0.95,
            "status": "resolved",
        })
        return result
    if len(norm_matches) > 1:
        result["status"] = "ambiguous"
        result["match_method"] = "normalized_exact"
        result["candidates"] = [_candidate(item, by_id, 0.95, "normalized_exact") for item in sorted(norm_matches)]
        return result

    choices = list(indexes["normalized"].keys())
    fuzzy_keys = difflib.get_close_matches(normalized_name, choices, n=5, cutoff=0.72)
    candidates: list[dict[str, Any]] = []
    for key in fuzzy_keys:
        score = difflib.SequenceMatcher(None, normalized_name, key).ratio()
        for candidate_id in indexes["normalized"][key]:
            candidates.append(_candidate(candidate_id, by_id, score, "normalized_fuzzy"))
    if candidates:
        result["status"] = "candidates"
        result["match_method"] = "normalized_fuzzy"
        result["candidates"] = sorted(candidates, key=lambda item: item["score"], reverse=True)
        result["confidence"] = result["candidates"][0]["score"]
    return result


def upsert_by_game_id(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

