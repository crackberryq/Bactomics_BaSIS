"""Launch the optional single-user interface on loopback only."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from .environment_setup import workspace_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="~/BaSIS-workspace")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Run the local interface inside Linux or WSL2")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 through 65535")
    if importlib.util.find_spec("streamlit") is None:
        print('Install the optional interface with: python -m pip install "bactromics-basis[ui,excel]"', file=sys.stderr)
        return 2
    root = workspace_path(args.workspace)
    root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-I", "-m", "streamlit", "run", str(Path(__file__).with_name("gui_app.py")),
               "--server.address", "127.0.0.1", "--server.port", str(args.port),
               "--server.headless", "true", "--browser.gatherUsageStats", "false",
               "--server.fileWatcherType", "none"]
    print(f"BaSIS interface: http://localhost:{args.port} | Workspace: {root}", flush=True)
    print("Analysis jobs continue if the browser or interface closes. Reopen this workspace to reconnect.", flush=True)
    return subprocess.call(command, env={**os.environ, "BASIS_GUI_WORKSPACE": str(root)})


if __name__ == "__main__":
    raise SystemExit(main())
