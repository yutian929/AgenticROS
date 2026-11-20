"""Copy one stopped legacy workspace to runs/workspace; never remove/merge sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from prepare_workspace import ROOT, IMPORT


def manifest(root):
    return {str(p.relative_to(root)): ('link', str(p.readlink())) if p.is_symlink() else
            ('file', hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else ('directory',)
            for p in root.rglob('*')}


def migrate(source=None, volume=None):
    target=ROOT/'runs/workspace'
    if target.exists() or target.is_symlink(): raise ValueError('destination exists; refusing merge or overwrite')
    if (ROOT/'runs').is_symlink(): raise ValueError('runs must not be a symlink')
    ids=subprocess.check_output(['docker','ps','-q'],text=True).split()
    containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True)) if ids else []
    source=Path(source).resolve() if source else None
    if source and not source.is_dir(): raise ValueError('source directory does not exist')
    if volume:
        subprocess.run(['docker','volume','inspect',volume],check=True,stdout=subprocess.DEVNULL)
    for container in containers:
        for mount in container.get('Mounts',[]):
            if (volume and mount.get('Name')==volume) or (source and mount.get('Type')=='bind' and
                    (source==Path(mount['Source']).resolve() or source in Path(mount['Source']).resolve().parents or Path(mount['Source']).resolve() in source.parents)):
                raise ValueError('source has an active container; stop that Agent before migration')
    target.parent.mkdir(exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.migration-',dir=target.parent))
    if source:
        before=manifest(source)
        shutil.copytree(source,staging,dirs_exist_ok=True,symlinks=True)
        if before!=manifest(source) or before!=manifest(staging): raise ValueError('verification failed; staging retained')
    else:
        import os
        subprocess.run(['docker','run','--rm','--network','none','--user','0',
                        '--mount','type=volume,src='+volume+',dst=/source,readonly',
                        '--mount','type=bind,src='+str(staging)+',dst=/destination',
                        '--entrypoint','python3','agenticros-server:single','-c',IMPORT,str(os.getuid()),str(os.getgid())],check=True)
    for path in [staging,*staging.rglob('*')]:
        if path.is_symlink(): continue
        mode=stat.S_IMODE(path.stat().st_mode) | stat.S_IRGRP | stat.S_IWGRP
        if path.is_dir(): mode |= stat.S_IXGRP | stat.S_ISGID
        elif mode & stat.S_IXUSR: mode |= stat.S_IXGRP
        path.chmod(mode)
    staging.rename(target)
    print('Verified copy: '+str(target)+'; source retained')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--from-directory'); group.add_argument('--from-volume')
    args=p.parse_args();migrate(args.from_directory,args.from_volume)
