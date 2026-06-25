from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Placeholder for read-only Steam installation scanning.")
    parser.add_argument("--steam-root", help="Optional Steam library root to scan read-only.")
    parser.add_argument("--timestamp", help="Timestamp used in preview filenames.")
    parser.parse_args()
    print("Steam installed-state scanner skeleton is present. It must remain read-only when implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

