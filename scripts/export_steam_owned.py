from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from vaultlib import ensure_parent, resolve_root, write_json


STEAM_OWNED_ENDPOINT = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
DEFAULT_FIELDS = [
    "appid",
    "name",
    "playtime_forever",
    "playtime_2weeks",
    "rtime_last_played",
    "has_community_visible_stats",
    "playtime_windows_forever",
    "playtime_mac_forever",
    "playtime_linux_forever",
    "playtime_deck_forever",
    "img_icon_url",
]


def _timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def _load_local_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"Local Steam config must be a JSON object: {path}")
    return payload


def load_credentials(config_path: Path) -> tuple[str, str]:
    local_config = _load_local_json(config_path)
    api_key = os.environ.get("STEAM_API_KEY") or local_config.get("api_key")
    steam_id = os.environ.get("STEAM_ID") or os.environ.get("STEAM_ID64") or local_config.get("steam_id")
    if not api_key or not steam_id:
        raise RuntimeError(
            "Missing Steam credentials. Set STEAM_API_KEY and STEAM_ID, "
            f"or create local ignored config: {config_path}"
        )
    return str(api_key), str(steam_id)


def fetch_owned_games(api_key: str, steam_id: str, timeout: int) -> dict[str, Any]:
    params = {
        "key": api_key,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }
    url = f"{STEAM_OWNED_ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "MyGameStory/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read().decode(charset)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Steam API response was not a JSON object")
    return data


def extract_games(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response")
    if not isinstance(response, dict):
        return []
    games = response.get("games")
    if not isinstance(games, list):
        return []
    return [game for game in games if isinstance(game, dict)]


def write_owned_csv(path: Path, games: list[dict[str, Any]]) -> None:
    extra_fields = sorted({key for game in games for key in game if key not in DEFAULT_FIELDS})
    fieldnames = DEFAULT_FIELDS + extra_fields
    ensure_parent(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for game in sorted(games, key=lambda item: str(item.get("name") or "").casefold()):
            writer.writerow({field: game.get(field, "") for field in fieldnames})


def export_owned_library(root: Path, timestamp: str, config_path: Path, output_dir: Path, timeout: int) -> dict[str, Path]:
    api_key, steam_id = load_credentials(config_path)
    raw_output = output_dir / f"{timestamp}.raw.json"
    timestamped_csv = output_dir / f"{timestamp}.steam_library_raw.csv"
    latest_csv = output_dir / "steam_library_raw.csv"
    metadata_output = output_dir / f"{timestamp}.export_metadata.json"
    existing_timestamped_outputs = [path for path in (raw_output, timestamped_csv, metadata_output) if path.exists()]
    if existing_timestamped_outputs:
        existing = ", ".join(str(path) for path in existing_timestamped_outputs)
        raise FileExistsError(f"Timestamped export output already exists: {existing}")

    payload = fetch_owned_games(api_key, steam_id, timeout=timeout)
    games = extract_games(payload)

    write_json(raw_output, payload)
    write_owned_csv(timestamped_csv, games)
    shutil.copyfile(timestamped_csv, latest_csv)

    metadata = {
        "source": "steam_owned_api",
        "timestamp": timestamp,
        "steam_id": steam_id,
        "raw_output": str(raw_output),
        "csv_output": str(latest_csv),
        "timestamped_csv_output": str(timestamped_csv),
        "game_count": len(games),
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json(metadata_output, metadata)
    return {
        "raw_json": raw_output,
        "csv": latest_csv,
        "timestamped_csv": timestamped_csv,
        "metadata": metadata_output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Steam owned-library raw JSON and flat CSV.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to this repository root.")
    parser.add_argument("--timestamp", default=None, help="Output timestamp. Defaults to current local time.")
    parser.add_argument("--config", default=None, help="Local JSON config with api_key and steam_id.")
    parser.add_argument("--output-dir", default=None, help="Output directory for raw JSON and CSV.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    timestamp = args.timestamp or _timestamp()
    config_path = Path(args.config).resolve() if args.config else root / "scripts" / "steam_api.local.json"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else root / "data" / "imports" / "steam_owned"

    outputs = export_owned_library(root, timestamp, config_path, output_dir, args.timeout)
    metadata = json.loads(outputs["metadata"].read_text(encoding="utf-8-sig"))
    print(f"Exported {metadata['game_count']} Steam owned records.")
    print(f"CSV: {outputs['csv']}")
    print(f"Raw JSON: {outputs['raw_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
