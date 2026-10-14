#!/usr/bin/env python3
"""Prepare local, unreviewed screening-reference catalogue; run no analyses."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import csv
import hashlib
import json
import os
import tempfile
import yaml
from basis.config import load_config
from basis.references import read_fasta, write_fasta, load_catalog, PROTEIN_ALPHABET
from BaSIS_run_context import clean_config


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def build_catalog(card, vfdb, amr, destination):
    seqs, rows, sources, counts = {}, [], [], Counter()
    fields = ['reference_id','accession','family','comparison_group','role',
              'review_status','source','sequence_sha256','source_description','source_model_type']
    def add(namespace, key, sequence, accession, label, source, model=''):
        sequence = ''.join(sequence.split()).upper()
        if not sequence or not PROTEIN_ALPHABET.fullmatch(sequence):
            raise ValueError(f'Invalid protein sequence: {namespace}/{key}')
        rid = namespace + '_' + hashlib.sha256(str(key).encode()).hexdigest()[:24]
        if rid in seqs:
            raise ValueError(f'Duplicate source reference identifier: {namespace}/{key}')
        seqs[rid] = sequence
        rows.append(dict(reference_id=rid, accession=accession or str(key),
                         family=label or 'unassigned', comparison_group='unassigned',
                         role='reference', review_status='unreviewed', source=source,
                         sequence_sha256=hashlib.sha256(sequence.encode()).hexdigest(),
                         source_description=label, source_model_type=model))
        counts[namespace] += 1
    data = json.loads(card.read_text())
    for model_id, model in data.items():
        if not isinstance(model, dict) or 'model_sequences' not in model:
            continue
        entries = model['model_sequences'].get('sequence', {})
        if not isinstance(entries, dict):
            raise ValueError(f'Unsupported CARD sequence structure: {model_id}')
        for sid, entry in entries.items():
            protein = entry.get('protein_sequence') or {}
            sequence = protein.get('sequence')
            if not sequence:
                counts['CARD_entries_without_protein'] += 1
                continue
            add('CARD', f'{model_id}:{sid}', sequence, protein.get('accession'),
                model.get('ARO_name', ''), f'{card.resolve().as_uri()}#model={model_id};sequence={sid}',
                model.get('model_type', ''))
    if not counts['CARD']:
        raise ValueError('No CARD proteins extracted; refusing empty/unsupported input')
    for namespace, path in [('VFDB', vfdb), ('AMRFinder', amr)]:
        if path is None:
            continue
        # Preserve exact source IDs; descriptions are source annotation, not reviewed functions.
        headers = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('>'):
                parts = line[1:].split(maxsplit=1)
                headers[parts[0]] = parts[1] if len(parts)>1 else parts[0]
        for key, seq in read_fasta(path).items():
            add(namespace, key, seq, key, headers[key], path.resolve().as_uri())
    for path in [card, vfdb] + ([amr] if amr else []):
        sources.append({'path':str(path.resolve()), 'sha256':sha(path)})
    destination.mkdir(parents=True, exist_ok=False)
    fasta = destination/'references.faa'; metadata = destination/'references.tsv'
    write_fasta(seqs, fasta)
    with metadata.open('w', newline='', encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t')
        writer.writeheader();writer.writerows(rows)
    catalogue=load_catalog(fasta,metadata)
    manifest={'created_at_utc':datetime.now(timezone.utc).isoformat(), 'counts':dict(counts),
              'source_files':sources, 'card_version':data.get('_version'),
              'fasta_sha256':sha(fasta), 'metadata_sha256':sha(metadata),
              'review_status':'unreviewed', 'comparison_groups':'unassigned',
              'limitations':['Reuses screening databases; not independent confirmation.',
                             'CARD model reference sequences need not themselves carry resistance variants.',
                             'No validated family comparator sets or outgroups supplied.']}
    (destination/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return catalogue, manifest


def main():
    project=Path(__file__).resolve().parent
    cfg=clean_config(load_config(project/'study-isolate1/config.deep-validation.yaml'))
    target=project/'study-isolate1/config.deep-resources.yaml'
    if target.exists():
        raise ValueError(f'{target} already exists; leaving it unchanged. Share its path/output for review.')
    root=project.parent/'db'
    card=root/'card/download-20261007/card.json'
    vfdb=root/'vfdb/setA-20261007T204847989999Z/utf8-20261007T205409255854Z/VFDB_setA_pro.utf8.fas'
    amr=root/'amrfinder/latest/AMRProt.fa'
    if not amr.is_file():
        print('AMRFinder AMRProt.fa unavailable; catalogue will use CARD and VFDB only.')
        amr=None
    pfam=root/'antismash/pfam/35.0/Pfam-A.hmm'
    for path in [card,vfdb,pfam]+[Path(str(pfam)+s) for s in ('.h3f','.h3i','.h3m','.h3p')]:
        if not path.is_file() or not path.stat().st_size:
            raise FileNotFoundError(f'Required resource missing/empty: {path}')
    env=project/'.basis-envs/basis-deepsafe'
    for exe in ('blastp','hmmscan'):
        if not os.access(env/'bin'/exe,os.X_OK):
            raise FileNotFoundError(f'Executable missing: {env / "bin" / exe}')
    parent=project/'study-isolate1/deep-reference-catalogues';parent.mkdir(exist_ok=True)
    destination=parent/datetime.now(timezone.utc).strftime('screening-%Y%m%dT%H%M%S%fZ')
    catalog,manifest=build_catalog(card,vfdb,amr,destination)
    cfg['deep'].update(reference_fasta=str(catalog.fasta), reference_metadata=str(catalog.metadata_path),
                       domain_database=str(pfam.resolve()), reference_search_tool='blastp',
                       command_prefix=['conda','run','--no-capture-output','-p',str(env)],
                       domain_scan=True, phylogeny=False, include_exploratory=False,
                       maximum_exploratory_candidates=0)
    with target.open('x') as handle:yaml.safe_dump(cfg,handle,sort_keys=False)
    load_config(target)
    print('CATALOGUE READY:', dict(manifest['counts']))
    print('Reference count:',len(catalog.sequences))
    print('Manifest:',destination/'provenance.json')
    print('Config:',target)
    print('Pfam:',pfam.resolve())
    print('All imported comparators remain UNREVIEWED; no family anchors or outgroups asserted.')
    print('Phylogeny disabled until comparator coverage is reviewed. No analyses were run.')


if __name__=='__main__':
    main()
