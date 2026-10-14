#!/usr/bin/env python3
"""Reconcile existing GA-domain calls and prepare source-reviewed comparator seeds."""
import csv,hashlib,io,json
from pathlib import Path
import yaml
from basis.config import load_config
from basis.inputs import load_samples,validate_sample
from basis.deep import summarize_domain_architecture
from basis.references import load_catalog,validate_profiles,profile_for_candidate
from BaSIS_run_context import clean_config


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def safe_write(path,data):
    if isinstance(data,str):data=data.encode()
    if path.exists():
        if path.read_bytes()!=data:raise ValueError(f'Existing file differs; refusing overwrite: {path}')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as handle:handle.write(data)


def main():
    root=Path(__file__).resolve().parent
    config=clean_config(load_config(root/'study-isolate1/config.deep-ga-validation.yaml'))
    record_path=Path(config['outdir'])/'results/isolate1/deepsafe/result.json'
    record=json.loads(record_path.read_text())
    sample=next(s for s in load_samples(config['samples']) if s['sample_id']=='isolate1')
    if record.get('execution_status')!='completed' or record.get('input_hashes')!=validate_sample(sample)['input_hashes']:
        raise ValueError('Existing completed DeepSafe evidence does not match current inputs')
    panel=root/'resources/comparators/reviewed-v1'
    checksums=json.loads((panel/'checksums.json').read_text())
    for name,expected in checksums.items():
        path=panel/name
        if not path.is_file() or digest(path)!=expected:raise ValueError(f'Comparator package changed: {name}')
    profiles,errors=validate_profiles(panel/'profiles')
    if errors:raise ValueError('; '.join(errors))
    old=load_catalog(Path(config['deep']['reference_fasta']),Path(config['deep']['reference_metadata']))
    new=load_catalog(panel/'references.faa',panel/'references.tsv')
    sequences=dict(old.sequences);metadata=dict(old.metadata)
    for rid,seq in new.sequences.items():
        if rid in sequences:raise ValueError(f'Duplicate reference ID: {rid}')
        sequences[rid]=seq;metadata[rid]=new.metadata[rid]
    destination=root/'study-isolate1/reviewed-reference-v1'
    fasta=''.join('>'+rid+'\n'+seq+'\n' for rid,seq in sequences.items())
    fields=sorted({k for row in metadata.values() for k in row})
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,delimiter='\t')
    writer.writeheader();writer.writerows(metadata.values())
    safe_write(destination/'references.faa',fasta);safe_write(destination/'references.tsv',stream.getvalue())
    load_catalog(destination/'references.faa',destination/'references.tsv')
    report=[]
    print('No native tools run. Existing accepted GA hits regrouped; original results preserved.')
    for candidate in record.get('candidates',[]):
        if not candidate.get('selected'):continue
        domains=candidate.get('analyses',{}).get('domains',{})
        if domains.get('postfilter')!='model_GA_sequence_and_domain':
            raise ValueError('Expected update-6 GA-filtered domain evidence')
        architecture=summarize_domain_architecture(domains.get('hits',[]))
        profile=profile_for_candidate(candidate,profiles)
        report.append(dict(protein_id=candidate.get('protein_id'),candidate_id=candidate['candidate_id'],
                           architecture=architecture,comparator_profile=profile['profile_id']))
        print(candidate.get('protein_id'),'| accepted hits:',architecture['accepted_hit_count'],
              '| overlap regions:',architecture['overlap_region_count'],
              '| ambiguous regions:',architecture['ambiguous_region_count'],'| profile:',profile['profile_id'])
        for region in architecture['regions']:
            if region['annotation_count']>1:
                print('  ',str(region['start'])+'-'+str(region['end']),':',' / '.join(region['model_labels']))
    safe_write(destination/'domain_overlap_review.json',json.dumps(report,indent=2)+'\n')
    coverage=json.loads((panel/'coverage.json').read_text())
    print('Source-reviewed seed references:',len(new.sequences),'| profiles:',len(profiles))
    print('Review means source/identity audit; evidence basis and source curation status remain separate.')
    for row in coverage:
        if row['missing_comparison_groups']:print('OPEN GAP:',row['profile_id'],','.join(row['missing_comparison_groups']))
    manifest={'derived_from_deepsafe_result':str(record_path),'result_sha256':digest(record_path),
              'old_reference_fasta_sha256':digest(old.fasta),'old_reference_metadata_sha256':digest(old.metadata_path),
              'panel_checksums_sha256':digest(panel/'checksums.json'),
              'combined_fasta_sha256':digest(destination/'references.faa'),
              'combined_metadata_sha256':digest(destination/'references.tsv'),
              'note':'Derived overlap review only. New references have not yet been searched against isolate1.'}
    safe_write(destination/'provenance.json',json.dumps(manifest,indent=2)+'\n')
    config['deep'].update(reference_fasta=str(destination/'references.faa'),reference_metadata=str(destination/'references.tsv'),
                          profiles_dir=str(panel/'profiles'),phylogeny=False)
    config['outdir']=str(root/'study-isolate1/results-deep-reviewed')
    target=root/'study-isolate1/config.deep-reviewed.yaml'
    safe_write(target,yaml.safe_dump(config,sort_keys=False))
    print('Prepared config:',target)
    print('Overlap review:',destination/'domain_overlap_review.json')
    print('Reference audit:',panel/'references.tsv')
    print('Trees OFF. Broad family seed panels are not validated classifiers; named gaps remain unresolved.')

if __name__=='__main__':main()
