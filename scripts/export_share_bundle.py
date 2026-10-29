#!/usr/bin/env python3
"""Preview or export saved BaSIS results using short, Teams-friendly ZIP members."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "workflow/lib"), str(ROOT / "workflow/vendor")]

from safety.share_bundle import build_plan, export_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--isolate", type=Path, help="Select this isolate's safety directory")
    selection.add_argument("--source", type=Path, help="Select a saved results directory directly")
    parser.add_argument("--output", required=True, type=Path, help="New ZIP outside the source directory")
    parser.add_argument("--apply", action="store_true", help="Create the ZIP; otherwise only preview")
    parser.add_argument("--windows-root", help="Full Windows folder where the ZIP will be extracted")
    parser.add_argument("--cloud-root", help="Decoded server-relative SharePoint extraction folder")
    parser.add_argument("--max-windows-path", type=int, default=240)
    parser.add_argument("--allow-external-root", action="append", type=Path, default=[],
                        help="Explicitly allow linked results inside this directory; may be repeated")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.isolate / "safety" if args.isolate else args.source, args.output,
                          allow_external_roots=args.allow_external_root, windows_root=args.windows_root,
                          cloud_root=args.cloud_root, max_windows_path=args.max_windows_path)
        result = export_bundle(plan) if args.apply else plan.summary()
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "error", "message": str(error), "native_commands_run": False}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
