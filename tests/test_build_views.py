from __future__ import annotations

import csv
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


class BuildViewsTests(unittest.TestCase):
    def test_build_views_generates_expected_csv_headers(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "build_views.py"), "--root", str(PROJECT_ROOT)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assertEqual(_read_header(PROJECT_ROOT / "data" / "derived" / "games_flat.csv"), [
            "game_id",
            "steam_appid",
            "title_display",
            "title_zh",
            "title_en",
            "platform",
            "primary_category",
            "primary_category_display",
            "legacy_b_code",
            "curation_state",
            "curation_state_display",
            "favorite",
            "installed",
            "multiplayer_candidate",
            "completion_target",
            "playtime_hours",
            "playtime_2weeks_hours",
            "last_played_at",
            "progress_percent",
            "achievement_percent",
            "rating_score",
            "rating_stars",
            "comment_short",
            "paid_price",
            "currency",
            "price_per_hour",
            "play_plan",
            "manual_tags",
            "notes",
        ])
        self.assertEqual(_read_header(PROJECT_ROOT / "data" / "derived" / "category_stats.csv"), [
            "primary_category",
            "display_name",
            "count",
            "total_playtime_hours",
            "avg_rating",
            "completed_count",
            "unstarted_count",
        ])
        self.assertEqual(_read_header(PROJECT_ROOT / "data" / "derived" / "backlog_stats.csv"), [
            "curation_state",
            "count",
            "total_estimated_hours",
            "total_playtime_hours",
        ])


if __name__ == "__main__":
    unittest.main()
