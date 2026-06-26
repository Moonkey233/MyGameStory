from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from apply_confirmed_classifications import apply_preview as apply_classification_preview
from apply_confirmed_classifications import build_preview as build_classification_preview
from build_views import build_views
from validate_data import validate
from vaultlib import (
    TaxonomyChoice as CategoryChoice,
    coerce_int,
    format_taxonomy_choices,
    load_games_by_id,
    load_primary_category_choices,
    now_iso,
    parse_taxonomy_choice,
    read_jsonl,
    resolve_game,
    resolve_root,
    title_display,
    write_jsonl,
)


@dataclass(frozen=True)
class GameRow:
    index: int
    game_id: str
    steam_appid: int | None
    title: str
    primary_category: str | None
    classification_status: str | None


@dataclass(frozen=True)
class CategoryFilter:
    kind: str
    category: str | None = None


def default_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def _records_by_game_id(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for _, record in read_jsonl(path):
        game_id = record.get("game_id")
        if isinstance(game_id, str):
            records[game_id] = record
    return records


def load_rows(root: Path) -> list[GameRow]:
    games = load_games_by_id(root)
    classifications = _records_by_game_id(root / "data" / "manual" / "classifications.jsonl")
    rows: list[GameRow] = []
    for index, game_id in enumerate(sorted(games, key=lambda item: title_display(games[item]).casefold()), start=1):
        game = games[game_id]
        classification = classifications.get(game_id, {})
        steam_appid = coerce_int(game.get("steam_appid"))
        rows.append(GameRow(
            index=index,
            game_id=game_id,
            steam_appid=steam_appid,
            title=title_display(game),
            primary_category=classification.get("primary_category"),
            classification_status=classification.get("classification_status"),
        ))
    return rows


def parse_category_filter(raw_value: str, choices: list[CategoryChoice]) -> CategoryFilter:
    value = raw_value.strip()
    normalized = value.casefold()
    if normalized in {"", "all", "*"}:
        return CategoryFilter("all")
    if normalized in {"none", "null", "unclassified", "no_category", "missing"}:
        return CategoryFilter("unclassified")
    if normalized in {"pending", "needs_review", "suggested", "confirmed"}:
        return CategoryFilter("status", normalized)
    choice = parse_taxonomy_choice(value, choices)
    if choice is None:
        return CategoryFilter("unclassified")
    return CategoryFilter("category", choice.key)


def filter_rows(rows: list[GameRow], category_filter: CategoryFilter) -> list[GameRow]:
    if category_filter.kind == "all":
        return rows
    if category_filter.kind == "unclassified":
        return [row for row in rows if not row.primary_category]
    if category_filter.kind == "status":
        return [row for row in rows if row.classification_status == category_filter.category]
    if category_filter.kind == "category":
        return [row for row in rows if row.primary_category == category_filter.category]
    raise ValueError(f"unknown category filter kind: {category_filter.kind}")


def format_rows(rows: list[GameRow], limit: int | None = None) -> str:
    selected = rows[:limit] if limit else rows
    lines = [" idx  appid      game_id        category                 status       title"]
    lines.append("----  ---------  -------------  -----------------------  -----------  -----")
    for row in selected:
        appid = str(row.steam_appid or "")
        category = row.primary_category or "-"
        status = row.classification_status or "-"
        title = row.title[:80]
        lines.append(f"{row.index:>4}  {appid:<9}  {row.game_id:<13}  {category:<23}  {status:<11}  {title}")
    if limit and len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more")
    return "\n".join(lines)


def select_rows(raw_value: str, rows: list[GameRow]) -> list[GameRow]:
    value = raw_value.strip()
    if not value:
        return []
    if value.casefold() == "all":
        return rows

    by_index = {str(row.index): row for row in rows}
    by_appid = {str(row.steam_appid): row for row in rows if row.steam_appid is not None}
    by_game_id = {row.game_id.casefold(): row for row in rows}
    selected: list[GameRow] = []
    seen: set[str] = set()
    for token in [part.strip() for part in value.split(",") if part.strip()]:
        row = by_index.get(token) or by_appid.get(token) or by_game_id.get(token.casefold())
        if row is None:
            raise ValueError(f"unknown row/index/appid/game_id: {token}")
        if row.game_id not in seen:
            selected.append(row)
            seen.add(row.game_id)
    return selected


def resolve_target_rows(root: Path, raw_value: str, rows: list[GameRow]) -> list[GameRow]:
    value = raw_value.strip()
    if not value:
        return []
    row_by_id = {row.game_id: row for row in rows}
    if value.casefold().startswith("steam:"):
        row = row_by_id.get(value)
        return [row] if row else []
    if value.isdigit():
        row = row_by_id.get(f"steam:{value}")
        return [row] if row else []

    result = resolve_game(root, raw_name=value)
    if result.get("status") == "resolved" and result.get("game_id") in row_by_id:
        return [row_by_id[str(result["game_id"])]]

    candidates: list[GameRow] = []
    for candidate in result.get("candidates") or []:
        game_id = candidate.get("game_id")
        if isinstance(game_id, str) and game_id in row_by_id:
            candidates.append(row_by_id[game_id])
    return candidates


def parse_new_category(raw_value: str, choices: list[CategoryChoice]) -> CategoryChoice | None:
    value = raw_value.strip()
    if value.casefold() in {"none", "null", "clear", "pending", "unclassified"}:
        return None
    if not value:
        raise ValueError("empty category input cancels the edit")
    return parse_taxonomy_choice(value, choices)


def _base_classification(existing: dict[str, Any] | None, game_id: str) -> dict[str, Any]:
    record = dict(existing or {})
    record["game_id"] = game_id
    record.setdefault("curation_state", "unstarted")
    record.setdefault("favorite", False)
    record.setdefault("special_flags", [])
    record.setdefault("manual_tags", [])
    record.setdefault("platform_plan", "steam")
    record.setdefault("play_plan", "later")
    record.setdefault("classification_evidence", [])
    return record


def build_edit_records(root: Path, selected_rows: list[GameRow], choice: CategoryChoice | None, timestamp: str) -> list[dict[str, Any]]:
    classifications = _records_by_game_id(root / "data" / "manual" / "classifications.jsonl")
    updated_at = now_iso()
    records: list[dict[str, Any]] = []
    for row in selected_rows:
        record = _base_classification(classifications.get(row.game_id), row.game_id)
        evidence = list(record.get("classification_evidence") or [])
        evidence.append({
            "type": "manual_category_edit",
            "source": "edit_classification.py",
            "timestamp": timestamp,
            "from_primary_category": record.get("primary_category"),
            "to_primary_category": choice.key if choice else None,
        })
        record["primary_category"] = choice.key if choice else None
        record["classification_status"] = "confirmed" if choice else "pending"
        record["classification_evidence"] = evidence
        record["updated_at"] = updated_at
        records.append(record)
    return records


def _write_edit_input(root: Path, timestamp: str, records: list[dict[str, Any]]) -> Path:
    path = root / "data" / "imports" / "manual_classifications" / f"{timestamp}.category_edits.jsonl"
    if path.exists():
        raise FileExistsError(f"Category edit input already exists: {path}")
    write_jsonl(path, records)
    return path


def _confirm_apply(yes: bool, input_func: Callable[[str], str] = input) -> bool:
    if yes:
        return True
    answer = input_func("\nApply this category edit preview now? [Y/n] ").strip().casefold()
    return answer in {"", "y", "yes"}


def _prompt_target(root: Path, rows: list[GameRow], choices: list[CategoryChoice], input_func: Callable[[str], str]) -> list[GameRow]:
    while True:
        print("\n定位方式：")
        print("  1. 输入 appid / steam:<appid> / 名称")
        print("  2. 按当前类别列出，再选 idx/appid/game_id")
        raw_mode = input_func("选择 1/2，或 q 退出: ").strip().casefold()
        if raw_mode in {"q", "quit"}:
            return []
        if raw_mode == "1":
            raw_target = input_func("输入 appid、game_id 或名称: ")
            candidates = resolve_target_rows(root, raw_target, rows)
            if not candidates:
                print("没有找到匹配游戏。")
                continue
            if len(candidates) == 1:
                print(format_rows(candidates))
                return candidates
            print("找到多个候选：")
            print(format_rows(candidates))
            raw_select = input_func("选择 idx/appid/game_id: ")
            return select_rows(raw_select, candidates)
        if raw_mode == "2":
            print("\n当前类别可输入数字、B.04、slug；也可输入 pending / unclassified / all。")
            print(format_taxonomy_choices(choices))
            raw_filter = input_func("当前类别: ")
            try:
                category_filter = parse_category_filter(raw_filter, choices)
            except ValueError as exc:
                print(exc)
                continue
            filtered = filter_rows(rows, category_filter)
            if not filtered:
                print("这个筛选下没有游戏。")
                continue
            print(format_rows(filtered, limit=200))
            raw_select = input_func("选择要修改的 idx/appid/game_id；多个用逗号，all=全部: ")
            try:
                return select_rows(raw_select, filtered)
            except ValueError as exc:
                print(exc)
                continue
        print("请输入 1、2 或 q。")


def _prompt_new_category(choices: list[CategoryChoice], input_func: Callable[[str], str]) -> CategoryChoice | None:
    print("\n新类别输入方式：数字 1-17、B.04、英文 slug。")
    print("输入 clear/none/pending 可清空 B 类并标为 pending；直接回车取消。")
    print(format_taxonomy_choices(choices))
    while True:
        raw_value = input_func("新类别: ")
        if raw_value.strip() == "":
            raise KeyboardInterrupt("cancelled")
        if raw_value.strip() == "?":
            print(format_taxonomy_choices(choices))
            continue
        try:
            return parse_new_category(raw_value, choices)
        except ValueError as exc:
            print(f"{exc}；输入 ? 查看可选分类。")


def run(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    timestamp = args.timestamp or default_timestamp()
    choices = load_primary_category_choices(root)
    rows = load_rows(root)

    if args.from_category:
        category_filter = parse_category_filter(args.from_category, choices)
        filtered_rows = filter_rows(rows, category_filter)
        print(format_rows(filtered_rows, limit=args.limit))
        if args.list_only:
            return 0
        selected_rows = select_rows(args.select or input("选择要修改的 idx/appid/game_id；多个用逗号，all=全部: "), filtered_rows)
    elif args.appid or args.game_id or args.name:
        raw_target = args.game_id or args.appid or args.name or ""
        selected_rows = resolve_target_rows(root, raw_target, rows)
        if len(selected_rows) > 1:
            print(format_rows(selected_rows))
            selected_rows = select_rows(args.select or input("选择 idx/appid/game_id: "), selected_rows)
    else:
        selected_rows = _prompt_target(root, rows, choices, input)

    if not selected_rows:
        print("No games selected.")
        return 0

    print("\n将修改这些游戏：")
    print(format_rows(selected_rows))

    if args.new_category:
        new_choice = parse_new_category(args.new_category, choices)
    else:
        try:
            new_choice = _prompt_new_category(choices, input)
        except KeyboardInterrupt:
            print("Cancelled.")
            return 0

    records = build_edit_records(root, selected_rows, new_choice, timestamp)
    input_path = _write_edit_input(root, timestamp, records)
    preview_path = build_classification_preview(root, input_path, timestamp)
    print(f"\nCategory edit input: {input_path}")
    print(f"Classification preview: {preview_path}")

    if args.preview_only:
        print("Preview-only mode: no source files were updated.")
        return 0
    if not _confirm_apply(args.yes):
        print("Stopped before applying preview.")
        return 0

    apply_classification_preview(root, preview_path)
    print("Applied classification preview.")
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
        print(f"Edit completed with validation errors: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 1
    print(f"Validation passed: 0 error(s), {len(warnings)} warning(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactively edit official B-category classifications through a preview.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--timestamp", default=None, help="Timestamp for input and preview filenames.")
    parser.add_argument("--appid", default=None, help="Steam appid to edit.")
    parser.add_argument("--game-id", default=None, help="Full game_id to edit, for example steam:292030.")
    parser.add_argument("--name", default=None, help="Game title or alias to resolve.")
    parser.add_argument("--from-category", default=None, help="List games from this category/status before selecting.")
    parser.add_argument("--select", default=None, help="Selection from a listed set: idx, appid, game_id, comma list, or all.")
    parser.add_argument("--new-category", default=None, help="New category: number, B.xx, slug, or clear/none/pending.")
    parser.add_argument("--list-only", action="store_true", help="Only list games for --from-category.")
    parser.add_argument("--limit", type=int, default=200, help="Maximum rows to print when listing.")
    parser.add_argument("--preview-only", action="store_true", help="Generate input and preview, then stop before applying.")
    parser.add_argument("--yes", action="store_true", help="Apply the generated preview without asking again.")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
