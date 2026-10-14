"""Seal bundled package assets and build wheel/sdist without resolving dependencies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
if Path.cwd().resolve() != root:
    raise SystemExit('Run from the source directory containing pyproject.toml')
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True
from basis import __version__
from basis.release import runtime_hashes

manifest = {'schema':'basis-package-manifest-v1', 'version':__version__,
            'scope':'Python source and bundled runtime assets; external dependencies and data excluded',
            'sha256':runtime_hashes(root/'basis')}
path = root/'basis/resources/release-manifest.json'
path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
from setuptools.build_meta import build_wheel, build_sdist
dist = root/'dist'
dist.mkdir(exist_ok=True)
names = [build_wheel(str(dist)), build_sdist(str(dist))]
for name in names:
    artifact = dist/name
    print(name, hashlib.sha256(artifact.read_bytes()).hexdigest())
