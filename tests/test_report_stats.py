from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from report_stats import build_report, format_markdown  # noqa: E402


class ReportStatsTests(unittest.TestCase):
    def test_build_report_contains_core_sections(self) -> None:
        report = build_report(PROJECT_ROOT, limit=3, recent_days=30)

        self.assertIn("summary", report)
        self.assertIn("category_stats", report)
        self.assertIn("recently_added", report)
        self.assertIn("recently_played", report)
        self.assertIn("snapshot_changes", report)
        self.assertGreaterEqual(report["summary"]["total_games"], 0)

    def test_format_markdown_contains_useful_headers(self) -> None:
        report = build_report(PROJECT_ROOT, limit=1, recent_days=30)
        markdown = format_markdown(report)

        self.assertIn("# MyGameStory Stats", markdown)
        self.assertIn("## Category Stats", markdown)
        self.assertIn("## Latest Snapshot Changes", markdown)


if __name__ == "__main__":
    unittest.main()
