from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.pip_repository_search import (
    build_repository_search_command, parse_repository_search,
)


class PipPopularityTests(unittest.TestCase):
    def test_public_popularity_places_common_matches_first(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "index.html").write_text(
            "<a href='openai/'>openai</a>"
            "<a href='openpyxl/'>openpyxl</a>"
            "<a href='open obscure/'>open-obscure</a>",
            encoding="utf-8",
        )
        popularity = root / "popularity.json"
        popularity.write_text(json.dumps({"rows": [
            {"project": "openpyxl", "download_count": 1_000_000},
            {"project": "openai", "download_count": 10_000},
        ]}), encoding="utf-8")
        environment = dict(os.environ)
        environment["PIP_INDEX_URL"] = root.as_uri() + "/"
        environment["REMOTE_LINUX_PIP_POPULARITY_URL"] = popularity.as_uri()
        environment.pop("PIP_EXTRA_INDEX_URL", None)

        result = subprocess.run(
            build_repository_search_command("python3", "open", 20),
            shell=True, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=environment, timeout=20, check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = parse_repository_search(result.stdout)["rows"]
        self.assertEqual([row["name"] for row in rows[:2]], ["openpyxl", "openai"])
        self.assertEqual(rows[0]["popularity_rank"], 0)


if __name__ == "__main__":
    unittest.main()
