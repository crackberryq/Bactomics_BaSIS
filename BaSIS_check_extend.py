#!/usr/bin/env python3
"""Read-only Extend preflight; no models downloaded or inference run."""
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
env=root/'.basis-envs/basis-pathogenfinder2'
print('Extend environment:',env,'| exists:',(env/'conda-meta').is_dir())
if (env/'conda-meta').is_dir():
 code='''import json,importlib.metadata as m,importlib.util,shutil
r={'pathogenfinder2_executable':shutil.which('pathogenfinder2')}
for name in ['PathogenFinder2','torch','transformers','sentencepiece']:
 try:r[name]=m.version(name)
 except m.PackageNotFoundError:r[name]='not installed'
if importlib.util.find_spec('torch'):
 import torch
 r.update(cuda_available=torch.cuda.is_available(),torch_cuda=torch.version.cuda)
 if torch.cuda.is_available():r['gpu']=torch.cuda.get_device_name(0);r['vram_bytes']=torch.cuda.get_device_properties(0).total_memory
print(json.dumps(r,indent=2))'''
 subprocess.run(['conda','run','--no-capture-output','-p',str(env),'python','-c',code],check=True)
else:
 print('Runtime and CUDA compatibility not yet verified in the Extend environment.')
print('Local model folders:')
for name in ['pathogenfinder2','prott5','prot_t5']:
 p=Path.home()/'bactomics/db'/name
 print(p,'| exists:',p.exists())
print('No installation, download, training, or inference performed.')
