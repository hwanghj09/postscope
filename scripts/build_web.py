"""Build a complete static app with a pinned, self-hosted browser Python runtime."""
import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from postscope.public_snapshot import materialize,publish_files,publish_pending

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'web-dist'


def add_package(bundle,name):
    directory=Path(next(iter(importlib.util.find_spec(name).submodule_search_locations)))
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in {'.so','.pyd','.pyc'}:continue
        if name!='tzdata' and path.suffix not in {'.py','.txt'}:continue
        bundle.write(path,Path(name)/path.relative_to(directory))


def build():
    if OUTPUT.exists():shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()
    for name in ['static','assets']:
        shutil.copytree(ROOT/'postscope'/name,OUTPUT/name)
    runtime=ROOT/'node_modules'/'pyodide'
    if not runtime.is_dir():raise RuntimeError('Run npm ci --ignore-scripts before building.')
    vendor=OUTPUT/'vendor'/'pyodide';vendor.mkdir(parents=True)
    for name in ['pyodide.js','pyodide.asm.js','pyodide.asm.wasm','pyodide-lock.json','python_stdlib.zip']:
        shutil.copy2(runtime/name,vendor/name)
    lock=json.loads((runtime/'pyodide-lock.json').read_text())
    package=lock['packages']['sqlite3'];filename=package['file_name']
    cache=ROOT/'node_modules'/'.cache'/'postscope';cache.mkdir(parents=True,exist_ok=True)
    wheel=cache/filename
    if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest()!=package['sha256']:
        with urllib.request.urlopen('https://cdn.jsdelivr.net/pyodide/v0.28.3/full/'+filename,timeout=60) as response:
            content=response.read()
        if hashlib.sha256(content).hexdigest()!=package['sha256']:raise RuntimeError('Browser SQLite checksum mismatch')
        wheel.write_bytes(content)
    shutil.copy2(wheel,vendor/filename)
    reports=ast.parse((ROOT/'postscope/services/reports.py').read_text())
    functions=[ast.unparse(node) for node in reports.body if isinstance(node,ast.FunctionDef) and node.name in {'public_snapshot','csv_bytes','create_report'}]
    browser_reports='''import csv,io,json,zipfile,base64
from pathlib import Path
from jinja2 import Environment,FileSystemLoader,select_autoescape
from ..storage import now,uid
ROOT=Path(__file__).resolve().parents[1]
PNG_IMAGES={}
def png_charts(result):return PNG_IMAGES
def get_plotlyjs():return (ROOT/'static'/'plotly.min.js').read_text()
'''+ '\n\n'.join(functions)
    with zipfile.ZipFile(OUTPUT/'engine.zip','w',zipfile.ZIP_DEFLATED) as bundle:
        for name in ['ijson','jinja2','markupsafe','tzdata']:add_package(bundle,name)
        for name in ['__init__.py','storage.py','demo.py','browser_engine.py','adapters/__init__.py','adapters/desktop_json.py','services/__init__.py','services/analysis.py','services/normalizer.py','templates/report.html','assets/NanumGothic-Regular.ttf','static/plotly.min.js']:
            bundle.write(ROOT/'postscope'/name,'postscope/'+name)
        bundle.writestr('postscope/services/browser_reports.py',browser_reports)
    with tempfile.TemporaryDirectory() as directory:
        dataset_file=ROOT/'public'/'dataset.json'
        if dataset_file.exists():
            dataset=json.loads(dataset_file.read_text(encoding='utf-8'))
            if dataset.get('is_demo') or dataset.get('source') in {'demo','synthetic'}:
                publish_pending(OUTPUT)
            else:
                store,workspace,chat=materialize(dataset,directory)
                from datetime import datetime,timedelta
                from zoneinfo import ZoneInfo
                zone=ZoneInfo('Asia/Seoul')
                start=datetime.fromisoformat(dataset['requested_start_utc']).astimezone(zone).date().isoformat()
                end=(datetime.fromisoformat(dataset['requested_end_utc'])-timedelta(microseconds=1)).astimezone(zone).date().isoformat()
                source=json.loads((ROOT/'public'/'source.json').read_text(encoding='utf-8'))
                publish_files(store,workspace,chat,OUTPUT,start,end,analysis_start=source.get('analysis_start_date'))
        else:
            config=json.loads((ROOT/'public'/'source.json').read_text(encoding='utf-8'))
            publish_pending(OUTPUT,config.get('label','텔레그램 게시 기록'))
    html=(ROOT/'postscope/templates/index.html').read_text()
    html=html.replace('{{ token }}','static').replace('<meta name="postscope-token"','<meta name="postscope-mode" content="static"><meta name="postscope-token"')
    html=html.replace('href="/static/','href="./static/').replace('src="/static/','src="./static/')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()[:12]
    html=html.replace('src="./static/app.js"',f'src="./static/app.js?v={revision}"')
    html=html.replace('href="./static/style.css"',f'href="./static/style.css?v={revision}"')
    html=html.replace('로컬 웹 앱','일일 게시 기록 분석').replace('<span class="online-dot"></span>로컬','<span class="online-dot"></span>하루 1회 갱신')
    html=html.replace('내 PC에서 분석하는 텔레그램 게시 기록. PostScope 로컬 분석 도구.','매일 갱신되는 텔레그램 게시 시간·요일 통계.')
    (OUTPUT/'index.html').write_text(html)
    css=OUTPUT/'static/style.css';css.write_text(css.read_text().replace("url('/assets/","url('../assets/"))
    (OUTPUT/'.nojekyll').write_text('')
    (OUTPUT/'404.html').write_text(html)
    print(f'Static web app built: {OUTPUT} ({sum(p.stat().st_size for p in OUTPUT.rglob("*") if p.is_file()):,} bytes)')


if __name__=='__main__':build()
