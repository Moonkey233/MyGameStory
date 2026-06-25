from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Placeholder for Steam recent-play imports.")
    parser.add_argument("--input", help="Steam recent-play JSON or CSV input.")
    parser.add_argument("--timestamp", help="Timestamp used in preview filenames.")
    parser.parse_args()
    print("Steam recent-play import skeleton is present. Implement preview/apply before writing snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

