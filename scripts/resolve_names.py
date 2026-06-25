from __future__ import annotations

import argparse
import json

from vaultlib import resolve_game, resolve_root, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a Steam appid, game_id, or raw title to a game_id.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to the MyGameStory repository root.")
    parser.add_argument("--name", default=None, help="Raw game name to resolve.")
    parser.add_argument("--steam-appid", default=None, help="Steam appid to resolve.")
    parser.add_argument("--game-id", default=None, help="Known game_id to verify.")
    parser.add_argument("--source-file", default=None, help="Source file path included in the result.")
    parser.add_argument("--output", default=None, help="Optional JSONL output path to append the result.")
    args = parser.parse_args()

    root = resolve_root(args.root)
    result = resolve_game(
        root=root,
        raw_name=args.name,
        steam_appid=args.steam_appid,
        game_id=args.game_id,
        source_file=args.source_file,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        write_jsonl(root / args.output, [result], append=True)
    return 0 if result["status"] == "resolved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
