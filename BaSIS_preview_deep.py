#!/usr/bin/env python3
"""Prepare a focused DeepSafe configuration and preview existing evidence only."""
import json
from collections import Counter
from pathlib import Path
import yaml
from basis.config import load_config
from basis.inputs import load_samples
from basis.deep import select_candidates
from BaSIS_run_context import clean_config


def main():
    project = Path(__file__).resolve().parent
    source = project / 'study-isolate1/config.core.yaml'
    target = source.with_name('config.deep-validation.yaml')
    config = clean_config(load_config(source))
    config['deep']['include_exploratory'] = False
    config['deep']['maximum_exploratory_candidates'] = 0
    evidence = json.loads((Path(config['outdir'])/'evidence.json').read_text())
    samples = load_samples(config['samples'])
    sample = next(s for s in samples if s['sample_id'] == 'isolate1')
    records = [r for r in evidence['records'] if r.get('sample_id') == sample['sample_id']
               and r.get('execution_status') == 'completed']
    if not records:
        raise ValueError('No completed isolate1 evidence found')
    candidates = select_candidates(records, config['deep'], sample)
    if target.exists():
        if clean_config(load_config(target)) != config:
            raise ValueError(f'Refusing to overwrite different settings: {target}')
    else:
        with target.open('x') as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
    print('PREVIEW ONLY: no native analyses or reference downloads run.')
    print('Config:', target)
    print('Taxon:', sample.get('taxon'), '| source:', sample.get('taxon_source'))
    print('Queues:', dict(Counter((c['selection_queue'],c['selected']) for c in candidates)))
    for c in candidates:
        if not c['selected']:
            continue
        print(c.get('protein_id'), '|', c['selection_queue'], '|', c.get('gene'))
        for check in c['taxonomy_review']['checks']:
            print('  TAXON REVIEW:', check['status'], '| model taxon:', check['declared_model_taxon'])
    print('DEEP RESOURCES:')
    for key in ('profiles_dir','reference_fasta','reference_metadata','domain_database'):
        value=config['deep'].get(key)
        print(' ',key,':',value or 'NOT CONFIGURED', '| exists:', bool(value and Path(value).exists()))
    print('Core results were read as historical evidence; this preview does not assert cache validity.')


if __name__ == '__main__':
    main()
