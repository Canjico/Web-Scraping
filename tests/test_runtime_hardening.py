import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import Edital
import _pyc_bridge


class RuntimeHardeningTests(unittest.TestCase):
    def test_internal_tools_are_not_executed_as_scrapers(self):
        self.assertNotIn("run_tests.py", Edital.SCRIPTS)
        self.assertNotIn("health_check.py", Edital.SCRIPTS)
        self.assertNotIn("smart_crawler.py", Edital.SCRIPTS)
        self.assertNotIn("smart_fallback.py", Edital.SCRIPTS)

    def test_subprocess_timeout_returns_controlled_result(self):
        result = Edital._run_subprocess(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            cwd=str(ROOT),
            env=os.environ.copy(),
            timeout_seconds=1,
        )
        self.assertEqual(result.returncode, 124)
        self.assertTrue(result.timed_out)
        self.assertIn("[TIMEOUT]", result.stderr)

    def test_legacy_pyc_report_is_structured(self):
        report = _pyc_bridge.pyc_compatibility_report(str(ROOT / "FINEP.py"))
        self.assertIn("python", report)
        self.assertIn("existing", report)
        self.assertIn("compatible", report)
        self.assertIsInstance(report["existing"], list)
        self.assertIsInstance(report["compatible"], list)

    def test_wrappers_have_direct_smart_fallback(self):
        wrapper_files = [ROOT / name for name in Edital.SCRIPTS if (ROOT / name).read_text(encoding="utf-8", errors="ignore").find("load_module_from_pyc") >= 0]
        self.assertGreater(len(wrapper_files), 0)
        for path in wrapper_files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIn("run_smart_fallback_for_wrapper", text, path.name)


if __name__ == "__main__":
    unittest.main()
