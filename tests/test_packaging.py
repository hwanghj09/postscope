import hashlib
import importlib.util
from pathlib import Path
import zipfile
import pytest

script=Path(__file__).resolve().parents[1]/'scripts'/'package_release.py'
spec=importlib.util.spec_from_file_location('package_release',script)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_release_package_excludes_runtime_data_secrets_and_generated_files(tmp_path):
    root=tmp_path/'source';root.mkdir()
    (root/'pyproject.toml').write_text('[project]\nversion="1.0.0"\n')
    (root/'start.sh').write_text('#!/bin/sh\n')
    for directory in ['postscope','postscope/__pycache__','data','dist','docs','tests']:
        (root/directory).mkdir(parents=True,exist_ok=True)
    for path in ['postscope/app.py','docs/README.md','tests/example.py']:
        (root/path).write_text('safe')
    for path in ['.env','data/private.json','dist/old.zip','postscope/private.session','postscope/private.session-journal','postscope/secret.key','postscope/local.sqlite3','postscope/.env.local','postscope/__pycache__/app.pyc']:
        (root/path).write_text('SENSITIVE')
    archive=module.package(root,tmp_path/'release','v1.0.0')
    with zipfile.ZipFile(archive) as z:
        names=set(z.namelist())
        assert names=={'postscope/pyproject.toml','postscope/start.sh','postscope/postscope/app.py','postscope/docs/README.md','postscope/tests/example.py'}
        assert z.getinfo('postscope/start.sh').external_attr>>16&0o111==0o111
        assert all(b'SENSITIVE' not in z.read(n) for n in names)
    checksum=(archive.parent/'SHA256SUMS.txt').read_text().split()[0]
    assert checksum==hashlib.sha256(archive.read_bytes()).hexdigest()
    first=archive.read_bytes()
    module.package(root,tmp_path/'release','v1.0.0')
    assert archive.read_bytes()==first
    with pytest.raises(ValueError):module.package(root,tmp_path/'release','v2.0.0')
