"""Conservative whole-stage reuse with a persistence-stable JSON payload seal.

Legacy seals are misses: this module never upgrades or rewrites saved evidence.
"""
import copy
import json
import re
from pathlib import Path
from .util import sha256_file, hash_object

CACHE_POLICY = 'deep_cache_v2'

# Executed inside the same command prefix as native analyses. Hash the complete
# environment rather than relying on version strings or package declarations.
_PROBE = r'''
import hashlib,json,sys,shutil
from pathlib import Path
root=Path(sys.prefix).resolve()
if not (root/'conda-meta').is_dir():
 kind='python_venv' if sys.prefix != sys.base_prefix and (root/'pyvenv.cfg').is_file() else 'non_conda'
 print(json.dumps({'status':'unavailable','reason':'non_conda_environment','environment_type':kind,'prefix':str(root)}))
 sys.exit(0)
names=json.loads(sys.argv[1]); tools={}
for name in names:
 p=shutil.which(name)
 if not p:raise RuntimeError('Missing executable: '+name)
 p=Path(p).resolve()
 if not p.is_relative_to(root):raise RuntimeError('Executable outside fingerprinted environment: '+str(p))
 tools[name]=str(p)
digest=hashlib.sha256();count=0
for p in sorted(root.rglob('*')):
 if not p.is_file() or '__pycache__' in p.parts or p.suffix in {'.pyc','.pyo'}:continue
 if p.name in {'history','.conda_lock'} and 'conda-meta' in p.parts:continue
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 digest.update((str(p.relative_to(root))+'\0'+h.hexdigest()+'\n').encode());count+=1
print(json.dumps({'status':'available','prefix':str(root),'environment_sha256':digest.hexdigest(),'file_count':count,'executables':tools}))
'''

def engine_identity(config, runner):
    names=['hmmscan','mafft','iqtree']
    if config.get('family_placement'): names.append('epa-ng')
    if config.get('prosite_snapshot'): names.append('perl')
    mode=config.get('reference_search_tool','blastp')
    if mode in {'blastp','diamond'}: names.append(mode)
    names=[config.get('executables',{}).get(n,n) for n in names]
    if config.get('prosite_profiles'): names.append(config.get('executables',{}).get('pfscan','pfscanV3'))
    identity = json.loads(runner.run([config.get('python_executable','python'),'-c',_PROBE,json.dumps(names)]).stdout)
    if isinstance(identity, dict) and identity.get('status') == 'unavailable':
        return identity
    # An empty, legacy-shaped or malformed response must never enable reuse.
    if (not isinstance(identity, dict) or identity.get('status') != 'available'
            or not isinstance(identity.get('prefix'), str) or not Path(identity['prefix']).is_absolute()
            or not isinstance(identity.get('environment_sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', identity['environment_sha256']) is None
            or type(identity.get('file_count')) is not int or identity['file_count'] <= 0
            or not isinstance(identity.get('executables'), dict)
            or set(identity['executables']) != set(names)
            or any(not isinstance(p, str) or not Path(p).is_absolute()
                   or '..' in Path(p).parts or not Path(p).is_relative_to(identity['prefix'])
                   for p in identity['executables'].values())):
        raise ValueError('Incomplete or malformed DeepSafe engine fingerprint')
    return identity

def stable_records(records):
    result=copy.deepcopy(records)
    for r in result:
        for k in ('cache_hit','cache_checked_at'):r.pop(k,None)
    return result

def seal(record, work):
    paths=set(record.get('artifacts',[]))
    paths.update(str(p) for p in Path(work).rglob('*') if p.is_file())
    checks={str(p):sha256_file(p) for p in sorted(paths) if Path(p).is_file()}
    return {'policy':CACHE_POLICY,'checksums':checks,'payload_sha256':payload_hash(record)}

def _unique_object(pairs):
    """Reject keys which would collide when persisted as JSON object names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key in DeepSafe cache payload: ' + key)
        result[key] = value
    return result

def payload_hash(record):
    """Hash the JSON value that is saved, rather than Python-only key types.

In-memory position maps use integer keys; JSON reloads them as strings.
Normalize through the same value conversions as atomic_json before sorting.
The pairs hook prevents integer/string key collisions from losing evidence.
"""
    data={k:v for k,v in record.items() if k not in {'deep_cache','cache_hit','cache_checked_at'}}
    persisted = json.loads(
        json.dumps(data, default=str, allow_nan=False, separators=(',', ':')),
        object_pairs_hook=_unique_object,
    )
    return hash_object(persisted)

def reusable(record,key):
    if not isinstance(record,dict):return False
    cache=record.get('deep_cache',{})
    metrics=record.get('metrics',{})
    if not isinstance(cache,dict) or not isinstance(metrics,dict):return False
    if record.get('execution_status')!='completed' or record.get('cache_key')!=key:return False
    if record.get('upstream_incomplete') or metrics.get('failed_candidates',0):return False
    if cache.get('policy')!=CACHE_POLICY:return False
    checks=cache.get('checksums')
    if not isinstance(checks,dict) or not checks:return False
    try:
        artifacts=record.get('artifacts',[])
        if not isinstance(artifacts,list):return False
        if any(str(p) not in checks for p in artifacts):return False
        if cache.get('payload_sha256')!=payload_hash(record):return False
        return all(Path(p).is_file() and sha256_file(p)==h for p,h in checks.items())
    except (OSError,TypeError,ValueError):return False
