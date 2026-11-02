"""Package reviewed source only; runtime metadata and secrets are never traversed."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitignore','README.md','pyproject.toml','requirements.txt','uv.lock','start.sh','start.bat'}
SOURCE_DIRS={'.github','docs','postscope','scripts','tests'}
EXCLUDED={'.git','.venv','__pycache__','.pytest_cache','data','dist','test-results'}
FORBIDDEN_SUFFIXES={'.pyc','.log','.sqlite','.sqlite3','.db','.session','.pem','.key'}


def source_files(root):
    for name in sorted(ROOT_FILES):
        path=root/name
        if path.is_file() and not path.is_symlink():
            yield path
    for name in sorted(SOURCE_DIRS):
        directory=root/name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for parent,subdirs,filenames in os.walk(directory,followlinks=False):
            subdirs[:]=sorted(d for d in subdirs if d not in EXCLUDED and not (Path(parent)/d).is_symlink())
            for filename in sorted(filenames):
                path=Path(parent)/filename
                if path.is_symlink() or path.suffix.lower() in FORBIDDEN_SUFFIXES:
                    continue
                if filename.startswith('.env') or '.session' in filename:
                    continue
                yield path


def package(root,output_dir,tag=None):
    version=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if tag is not None and tag!='v'+version:
        raise ValueError(f'Tag {tag!r} must equal application version v{version}')
    output_dir.mkdir(parents=True,exist_ok=True)
    archive=output_dir/f'PostScope-v{version}.zip'
    files=list(source_files(root))
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for path in files:
            relative=path.relative_to(root)
            info=zipfile.ZipInfo((Path('postscope')/relative).as_posix(),date_time=(2026,1,1,0,0,0))
            info.create_system=3
            mode=0o755 if relative.as_posix()=='start.sh' else 0o644
            info.external_attr=(stat.S_IFREG|mode)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            bundle.writestr(info,path.read_bytes())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Archive integrity check failed')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output_dir/'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n',encoding='utf-8')
    print(f'{archive.name}: {len(files)} files, {archive.stat().st_size:,} bytes, SHA256 {digest}')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dist')
    parser.add_argument('--tag',default=None)
    args=parser.parse_args()
    package(ROOT,args.output_dir,args.tag)
