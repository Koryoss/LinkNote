import os
import threading
import time
import unittest
from unittest.mock import patch

import rag


class ConceptParallelTests(unittest.TestCase):
    def test_parallel_map_preserves_order_and_caps_workers_at_three(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def worker(value):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return value * 2

        with patch.dict(os.environ, {"CONCEPT_MAX_WORKERS": "9"}):
            result = rag._parallel_ordered(range(8), worker)

        self.assertEqual(result, [value * 2 for value in range(8)])
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 3)


if __name__ == "__main__":
    unittest.main()
