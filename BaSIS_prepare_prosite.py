#!/usr/bin/env python3
"""Download an unmodified, release-consistent PROSITE snapshot from SIB."""
import hashlib,json,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
import yaml
from basis.prosite import FILES,load_snapshot

def main():
    root=Path(__file__).resolve().parent
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot=Path.home()/'bactomics/db/prosite'/('snapshot-'+stamp)
    snapshot.mkdir(parents=True,exist_ok=False)
    base='https://ftp.expasy.org/databases/prosite/'
    sources={name:base+('ps_scan/' if name=='ps_scan.pl' else '')+name for name in FILES}
    for name,url in sources.items():
        print('Downloading:',name,flush=True)
        subprocess.run(['curl','--fail','--location','--retry','4','--connect-timeout','30','--max-time','600','--output',str(snapshot/name),url],check=True)
    release=re.search(r'Release (\d{4}_\d+)',(snapshot/'ps_reldt.txt').read_text())
    if not release:raise ValueError('Release metadata not recognized')
    manifest={'schema':'basis-prosite-snapshot-v1','release':release.group(1),
        'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),'source_urls':sources,
        'sha256':{n:hashlib.sha256((snapshot/n).read_bytes()).hexdigest() for n in FILES},
        'license_url':'https://prosite.expasy.org/prosite_license.html',
        'license_notice':'PROSITE data: CC BY-NC-ND 4.0. Original database files retained unmodified. ps_scan has its own GPL notice.',
        'scope':'Patterns only; no profiles, miniprofile validation or ProRule inference.'}
    (snapshot/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    patterns,metadata=load_snapshot(snapshot)
    config_path=root/'study-isolate1/config.deep-auto.yaml'
    cfg=yaml.safe_load(config_path.read_text())
    cfg['deep']['prosite_snapshot']=str(snapshot.resolve())
    target=root/'study-isolate1'/('config.deep-prosite-'+stamp+'.yaml')
    with target.open('x') as f:yaml.safe_dump(cfg,f,sort_keys=False)
    print('PROSITE ready:',metadata['release'],'| eligible patterns:',metadata['eligible_patterns'])
    print('Excluded frequent patterns:',metadata['excluded_frequent_patterns'],'| profiles:',metadata['excluded_profiles'])
    print('Snapshot:',snapshot.resolve());print('Config:',target)
    print('No sequences submitted online. No analyses run.')
if __name__=='__main__':main()
