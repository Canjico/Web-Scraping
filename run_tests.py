"""Smoke tests do hardening 10/10.

Uso:
  python run_tests.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
