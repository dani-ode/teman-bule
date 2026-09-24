"""Pytest configuration dan fixtures bersama."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pastikan src/ di path untuk semua test
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("APP_ENV", "test")
