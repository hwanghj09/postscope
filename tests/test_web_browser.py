"""The published app is served by a file server only; no FastAPI process exists."""
import functools
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading
import pytest
from playwright.sync_api import sync_playwright,expect
from postscope.storage import Store
from postscope.services.analysis import run_analysis

DIST=Path(__file__).resolve().parents[1]/'web-dist'


@pytest.fixture(scope='module')
def static_server():
    if not (DIST/'snapshot.json').exists():pytest.skip('Build the static website with scripts/build_web.py first')
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(DIST.parent)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}/{DIST.name}/'
    server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.fixture
def static_page(static_server):
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];network=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.context.on('request',lambda request:network.append((request.method,request.url)))
        page.goto(static_server)
        expected=json.loads((DIST/'snapshot.json').read_text(encoding='utf-8'))
        if expected['analysis']:
            expect(page.locator('#metric-total')).to_have_text(f"{expected['analysis']['metrics']['total']:,}건",timeout=20000)
        else:
            expect(page.locator('#empty-state')).to_be_visible(timeout=20000)
        yield page,network,expected
        assert not errors,errors
        assert not [request for request in network if '/api/' in request[1]],network
        assert all(method=='GET' for method,url in network),network
        browser.close()


def test_static_initial_view_needs_only_snapshot_and_assets(static_page):
    page,network,data=static_page
    assert not page.workers
    assert not [url for method,url in network if 'pyodide' in url or 'engine.zip' in url]
    expect(page.locator('.heading-actions [data-import]')).to_be_hidden()
    expect(page.locator('#new-workspace')).to_be_hidden()
    expect(page.locator('.workspace-picker')).to_be_hidden()
    expect(page.locator('.room-field')).to_be_hidden()
    expect(page.locator('#quality-strip')).to_be_hidden()
    expect(page.locator('#advanced-filters')).not_to_have_attribute('open','')
    if data['analysis']:
        expect(page.locator('#filter-start')).to_have_value(data['analysis']['config']['start'])
        expect(page.locator('#filter-summary')).to_have_text('전체 기간')
        expect(page.locator('#filter-start')).to_be_hidden()
        assert page.locator('#filter-section').bounding_box()['height']<70
        page.locator('[data-page="records"]').click()
        expect(page.locator('#records-body tr')).to_have_count(min(50,len(data['records'])))
    else:
        expect(page.locator('#empty-state h2')).to_have_text('실제 데이터 연결 대기')
        expect(page.locator('#page-dashboard')).to_be_hidden()
        expect(page.locator('#demo-banner')).to_be_hidden()
        expect(page.locator('#load-demo')).to_be_hidden()
        expect(page.locator('#header-export')).to_be_disabled()
    page.set_viewport_size({'width':360,'height':800})
    assert page.evaluate('document.body.scrollWidth')==360


def test_static_filters_use_same_engine_and_export_offline_report(static_page,tmp_path):
    page,network,data=static_page
    if not data['analysis']:pytest.skip('Actual Telegram source has not produced a snapshot yet')
    page.locator('#advanced-filters summary').click()
    page.locator('[name="content"]').select_option('photo')
    page.locator('#apply-filters').click()
    shutil.copy2(DIST/'snapshot.sqlite3',tmp_path/'postscope.sqlite3')
    store=Store(tmp_path)
    expected=run_analysis(store,{**data['analysis']['config'],'content':'photo'})
    expect(page.locator('#metric-total')).to_have_text(f"{expected['metrics']['total']:,}건",timeout=120000)
    assert page.workers
    page.locator('#header-export').click()
    with page.expect_download(timeout=120000) as download:
        page.locator('[data-export="html"]').click()
    report=tmp_path/'report.html';download.value.save_as(report)
    html=report.read_text(encoding='utf-8')
    assert '<script src=' not in html
    assert data['workspace']['chats'][0]['title'] in html
    expect(page.locator('[data-export="html"] h3')).to_have_text('분석 보고서')


def test_static_png_download_is_real_image(static_page,tmp_path):
    from PIL import Image
    page,network,data=static_page
    if not data['analysis']:pytest.skip('Actual Telegram source has not produced a snapshot yet')
    page.locator('#header-export').click()
    with page.expect_download(timeout=120000) as download:
        page.locator('[data-export="png"]').click()
    path=tmp_path/'heatmap.png';download.value.save_as(path)
    assert Image.open(path).size==(1760,660)


def test_history_is_optional_and_settings_fit_mobile(static_page):
    page,network,data=static_page
    if not data['analysis']:pytest.skip('Actual Telegram source has not produced a snapshot yet')
    page.set_viewport_size({'width':360,'height':800})
    page.locator('#advanced-filters summary').click()
    expect(page.locator('#filter-start')).to_be_visible()
    assert page.evaluate('document.body.scrollWidth')==360
    chat=data['workspace']['chats'][0]
    if chat.get('analysis_start')!=chat['period_start']:
        page.locator('[data-period="history"]').click()
        expect(page.locator('#filter-start')).to_have_value(chat['period_start'])
        page.locator('#apply-filters').click()
        total=data['analysis']['quality']['total_stored']-data['analysis']['quality']['service_excluded']
        expect(page.locator('#metric-total')).to_have_text(f'{total:,}건',timeout=120000)
    page.locator('#reset-filters').click()
    expect(page.locator('#filter-start')).to_have_value(data['analysis']['config']['start'])
    page.locator('#apply-filters').click()
    expect(page.locator('#metric-total')).to_have_text(f"{data['analysis']['metrics']['total']:,}건",timeout=120000)
    page.locator('#advanced-filters summary').click()
    expect(page.locator('#filter-start')).to_be_hidden()
