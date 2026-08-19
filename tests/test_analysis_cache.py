import os
import tempfile
import unittest
from unittest.mock import patch

import analysis_cache


class AnalysisCacheTests(unittest.TestCase):
    def test_json_cache_round_trip_and_bulk_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cache.sqlite3")
            with patch.dict(os.environ, {"ANALYSIS_CACHE_PATH": path}):
                analysis_cache.set_many_json("page", {"a": {"text": "서맥"}, "b": [1, 2]})
                self.assertEqual(analysis_cache.get_json("page", "a"), {"text": "서맥"})
                self.assertEqual(
                    analysis_cache.get_many_json("page", ["a", "b", "missing"]),
                    {"a": {"text": "서맥"}, "b": [1, 2]},
                )


if __name__ == "__main__":
    unittest.main()
