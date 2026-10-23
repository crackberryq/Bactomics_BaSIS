#!/usr/bin/env python3
"""Inspect standalone inputs and resources without running or downloading tools."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety.doctor import main

if __name__ == "__main__":
    raise SystemExit(main())
