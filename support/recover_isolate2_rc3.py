#!/usr/bin/env python3
"""One-shot recovery for the inspected isolate2 rc3 ProtT5 download orphan.

Read-only by default. --apply stops only the three inspected process identities
and their verified descendants, verifies the existing pinned local model, writes
a fresh timestamped config/audit, and starts the rc5 workflow. Nothing is deleted.
This is deliberately study-specific support, not generic orphan discovery.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

import yaml

PROJECT = Path('/home/jimi/bactomics/BaSIS_v0.3.0rc2')
MODEL_ROOT = Path('/home/jimi/bactomics/db/prott5')
REPOSITORY = 'Rostlab/prot_t5_xl_half_uniref50-enc'
REVISION = '94a6abc029ae13029317b140b7424e012bf8dfbf'
STAGING_NAME = 'prott5-fb6cb9cd34e543969493b730cfb7e9ef'
ROOT_PID, SHELL_PID, WORKER_PID = 118331, 118332, 118337
EXPECTED_PIDS = (ROOT_PID, SHELL_PID, WORKER_PID)
REQUIRED_FILES = {'config.json', 'spiece.model', 'pytorch_model.bin',
                  'tokenizer_config.json', 'special_tokens_map.json'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def under(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def verify_orphans(table, project):
    """Match the exact PIDs, current user, stage, arguments and inspected chain."""
    rows = {pid: table[pid] for pid in EXPECTED_PIDS if pid in table}
    if not rows:
        return None, {}
    if set(rows) != set(EXPECTED_PIDS):
        raise ValueError('Only part of the inspected orphan chain remains; no signals sent. '
                         'Inspect the remaining PIDs: ' + ', '.join(map(str, rows)))
    stage = (project / 'resources/staging' / STAGING_NAME).resolve(strict=True)
    for pid, process in rows.items():
        if process.uid != os.getuid() or Path(process.cwd).resolve() != stage:
            raise ValueError(f'PID {pid} no longer matches the inspected user/staging directory; no signals sent')
        executable = Path(process.argv[0]).name if process.argv else ''
        if pid == SHELL_PID:
            if executable != 'bash' or process.ppid != ROOT_PID:
                raise ValueError('Inspected shell identity or parent changed; no signals sent')
        else:
            if not executable.startswith('python') or REPOSITORY not in process.argv or REVISION not in process.argv:
                raise ValueError(f'PID {pid} no longer matches the pinned ProtT5 command; no signals sent')
            if str(stage) not in process.argv:
                raise ValueError(f'PID {pid} no longer names the inspected staging directory; no signals sent')
    if rows[WORKER_PID].ppid != SHELL_PID:
        raise ValueError('Inspected native worker parent changed; no signals sent')
    from basis.run_control import owned_descendants
    owned = owned_descendants(table, {ROOT_PID: rows[ROOT_PID]})
    if not set(EXPECTED_PIDS).issubset(owned):
        raise ValueError('Inspected process ancestry cannot be established; no signals sent')
    return rows[ROOT_PID], owned


def verify_model(root):
    """Verify every recorded model file; never accept an unrecorded checkpoint."""
    root = Path(root).resolve(strict=True)
    manifest_path = root / 'provenance.json'
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    if not isinstance(manifest, dict) or (manifest.get('repository'), manifest.get('revision')) != (REPOSITORY, REVISION):
        raise ValueError('Existing ProtT5 manifest does not identify the expected repository and pinned revision')
    model = (root / REVISION).resolve(strict=True)
    if not under(model, root):
        raise ValueError('Pinned model directory resolves outside the existing model root')
    for field in ('model_path', 'local_dir', 'path'):
        if manifest.get(field) is not None and Path(manifest[field]).resolve(strict=True) != model:
            raise ValueError('Existing model manifest path differs from its pinned revision directory: ' + field)
    recorded = manifest.get('sha256')
    if not isinstance(recorded, dict) or not REQUIRED_FILES.issubset(recorded):
        raise ValueError('Existing model manifest lacks required per-file SHA256 checksums')
    verified = {}
    for name, expected in sorted(recorded.items()):
        if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
            raise ValueError('Invalid model checksum entry')
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or not relative.parts:
            raise ValueError('Unsafe relative model path: ' + name)
        file = (model / name).resolve(strict=True)
        if not under(file, model) or not file.is_file() or not file.stat().st_size:
            raise ValueError('Missing, empty or escaping model file: ' + name)
        print('Verifying local model:', name, flush=True)
        actual = sha256(file)
        if actual != expected.lower():
            raise ValueError('Local ProtT5 file checksum mismatch: ' + name)
        verified[name] = actual
    if manifest_path.read_bytes() != original:
        raise ValueError('Local model provenance changed during verification')
    config = json.loads((model / 'config.json').read_text())
    if config.get('model_type') != 't5' or config.get('d_model') != 1024:
        raise ValueError('Local model configuration differs from the expected ProtT5 encoder')
    # Do not let an extra, unchecked checkpoint shadow the verified .bin file.
    checkpoints = {str(path.relative_to(model)) for path in model.rglob('*')
                   if path.is_file() and '.cache' not in path.relative_to(model).parts
                   and (path.suffix in {'.bin', '.safetensors', '.model', '.json'})}
    if not checkpoints.issubset(verified):
        raise ValueError('Unrecorded model checkpoint/tokenizer files: ' + ', '.join(sorted(checkpoints - verified.keys())))
    return model, {'manifest': str(manifest_path), 'manifest_sha256': hashlib.sha256(original).hexdigest(),
                   'repository': REPOSITORY, 'revision': REVISION, 'model': str(model), 'verified_sha256': verified,
                   'verification_scope': 'Matches local recorded download provenance; not independent vendor authentication'}


def scoped_workers(table, project, config_path, outdir, ignored=()):
    from basis.run_control import matches_config
    roots = [project / 'resources', project / 'study-isolate2', Path(outdir)]
    matches = []
    for process in table.values():
        if process.pid in {*ignored, os.getpid()}:
            continue
        relevant = any(under(process.cwd, root) for root in roots) or matches_config(process, config_path)
        for token in process.argv:
            argument = token.split('=', 1)[1] if token.startswith('--') and '=' in token else token
            if argument.startswith('/') and any(under(argument, root) for root in roots):
                relevant = True
        if relevant:
            matches.append(process.pid)
    return sorted(matches)


def prepare_config(project, config_path, raw, source_bytes, model, model_audit, stop_result):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = config_path.with_name('config.retry-rc5-' + stamp + '.yaml')
    audit_path = target.with_suffix('.recovery.json')
    outdir = config_path.parent / ('results-rc5-' + stamp)
    if target.exists() or audit_path.exists() or outdir.exists():
        raise ValueError('Fresh recovery paths already exist; nothing overwritten')
    updated = copy.deepcopy(raw)
    section = updated.setdefault('modules', {}).setdefault('pathogenfinder2', {})
    section.update(prot_t5_path=str(model), prot_t5_identity=REPOSITORY + '@' + REVISION)
    # Existing classifier weights configuration is preserved; if absent, the
    # normal automatic resource rule will copy the pinned tool's bundled weights.
    updated['outdir'] = str(outdir)
    updated.setdefault('support_recovery', {})['rc3_prott5'] = str(audit_path)
    rendered = yaml.safe_dump(updated, sort_keys=False)
    if config_path.read_bytes() != source_bytes:
        raise ValueError('Source configuration changed during recovery; nothing overwritten')
    audit = {'schema': 'basis-isolate2-rc3-recovery-v1', 'created_at_utc': stamp,
             'source_config': str(config_path), 'source_config_sha256': hashlib.sha256(source_bytes).hexdigest(),
             'output_config': str(target), 'output_config_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
             'output_directory': str(outdir), 'model': model_audit, 'stopping': stop_result,
             'original_files_changed': False, 'native_analyses_reused': False}
    with audit_path.open('x', encoding='utf-8') as handle:
        json.dump(audit, handle, indent=2)
        handle.write('\n')
    try:
        with target.open('x', encoding='utf-8') as handle:
            handle.write(rendered)
    except BaseException:
        audit_path.unlink()
        raise
    return target, audit_path, outdir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--apply', action='store_true', help='Stop the inspected orphan, write a fresh config, and run rc5')
    args = parser.parse_args(argv)
    from basis import __version__
    if __version__ != '0.3.0rc5':
        raise ValueError('Install BaSIS 0.3.0rc5 before using this support helper; loaded ' + __version__)
    from basis.config import load_config
    from basis.run_control import process_table, stop_invocation
    project = args.project.expanduser().resolve(strict=True)
    config_path = project / 'study-isolate2/config.retry-rc3.yaml'
    source_bytes = config_path.read_bytes()
    raw = yaml.safe_load(source_bytes)
    if not isinstance(raw, dict):
        raise ValueError('Study configuration must be a mapping')
    cfg = load_config(config_path)
    table = process_table()
    launcher, owned = verify_orphans(table, project)
    others = scoped_workers(table, project, config_path, cfg['outdir'], owned)
    if others:
        raise ValueError('Other active study/resource workers remain; no signals sent. PIDs: ' + ', '.join(map(str, others)))
    print('Inspected orphan:', 'already absent' if launcher is None else ', '.join(map(str, sorted(owned))), flush=True)
    print('Verifying previously downloaded local ProtT5 before changing anything...', flush=True)
    model, model_audit = verify_model(MODEL_ROOT)
    print('Verified pinned local model:', model, flush=True)
    if not args.apply:
        print('Read-only inspection complete. No signals, config writes, downloads or native analyses performed.')
        print('Run again with --apply to stop the verified orphan and start a fresh rc5 run.')
        return 0
    # Hashing a multi-gigabyte model can take time; recheck rather than signal
    # the stale initial process snapshot. Completion during verification is OK.
    current, current_owned = verify_orphans(process_table(), project)
    if current is not None:
        if launcher is None or any(pid not in owned or owned[pid].identity != current_owned[pid].identity
                                   for pid in EXPECTED_PIDS):
            raise ValueError('Inspected process identities changed during model verification; no signals sent')
        stop_result = stop_invocation(current)
    else:
        stop_result = {'status': 'already_absent', 'signals': []}
    if stop_result['status'] not in {'stopped', 'already_absent'}:
        raise ValueError('Inspected processes remain active: ' + json.dumps(stop_result))
    remaining = scoped_workers(process_table(), project, config_path, cfg['outdir'])
    if remaining:
        raise ValueError('Study/resource workers remain after cleanup; no new run started. PIDs: ' + ', '.join(map(str, remaining)))
    target, audit, outdir = prepare_config(project, config_path, raw, source_bytes, model, model_audit, stop_result)
    print('Recovery audit:', audit, flush=True)
    print('Fresh configuration:', target, flush=True)
    print('Fresh results:', outdir, flush=True)
    command = [sys.executable, '-I', '-m', 'basis', 'run', '-c', str(target),
               '-d', '-e', '-v', '--cores', '4', '--output', 'summary']
    print('Starting rc5; original configuration, results, partial download and model preserved.', flush=True)
    os.chdir(project)
    os.execv(sys.executable, command)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print('Recovery stopped:', exc, file=sys.stderr, flush=True)
        raise SystemExit(2)
