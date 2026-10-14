#!/usr/bin/env python3
"""Extend a verified PROSITE snapshot with release-matched ProRule data."""
import argparse,hashlib,json,shutil,subprocess
from datetime import datetime,timezone
from pathlib import Path
import yaml
from basis.prosite import FILES,load_snapshot
from basis.prosite_profiles import load_profile_snapshot

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config');args=parser.parse_args()
    root=Path(__file__).resolve().parent
    configs=list((root/'study-isolate1').glob('config.deep-prosite-*.yaml'))
    if not args.config and not configs:raise ValueError('Prepare the base PROSITE snapshot first')
    source=Path(args.config) if args.config else max(configs,key=lambda p:p.stat().st_mtime_ns)
    cfg=yaml.safe_load(source.read_text());old=Path(cfg['deep']['prosite_snapshot']).resolve()
    load_snapshot(old)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot=old.parent/('profiles-'+stamp);snapshot.mkdir(exist_ok=False)
    for name in FILES:shutil.copyfile(old/name,snapshot/name)
    url='https://ftp.expasy.org/databases/prosite/prorule.dat'
    print('Downloading release-matched ProRule data...',flush=True)
    subprocess.run(['curl','--fail','--location','--retry','4','--connect-timeout','30','--max-time','600','--output',str(snapshot/'prorule.dat'),url],check=True)
    manifest=json.loads((old/'provenance.json').read_text())
    manifest.setdefault('sha256',{})['prorule.dat']=hashlib.sha256((snapshot/'prorule.dat').read_bytes()).hexdigest()
    manifest.setdefault('source_urls',{})['prorule.dat']=url
    manifest['prorule_downloaded_at_utc']=datetime.now(timezone.utc).isoformat()
    manifest['derived_from']={'path':str(old),'manifest_sha256':hashlib.sha256((old/'provenance.json').read_bytes()).hexdigest()}
    manifest['scope']='Patterns and profiles; conservative single-position ProRule residue observations. Not a full ProRule interpreter.'
    (snapshot/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    profiles,rules,meta=load_profile_snapshot(snapshot)
    cfg['deep']['prosite_snapshot']=str(snapshot.resolve());cfg['deep']['prosite_profiles']=True
    cfg['deep'].setdefault('executables',{})['pfscan']='pfscanV3'
    target=source.parent/('config.deep-profiles-'+stamp+'.yaml')
    with target.open('x') as f:yaml.safe_dump(cfg,f,sort_keys=False)
    print('Ready:',meta['release'],'| profiles:',len(profiles),'| rules:',len(rules))
    print('Config:',target);print('Original snapshot preserved. No analyses run.')
if __name__=='__main__':main()
