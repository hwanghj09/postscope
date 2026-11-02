"""Real Chromium flows. Uses a temporary loopback-only server and synthetic files."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
import pytest
from playwright.sync_api import sync_playwright,expect


@pytest.fixture(scope='module')
def browser_server(tmp_path_factory):
    directory=tmp_path_factory.mktemp('browser')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','postscope','--port',str(port),'--data-dir',str(directory/'data')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(150):
            try:
                with urlopen(url+'/api/health',timeout=1) as response:
                    if response.status==200:break
            except OSError:time.sleep(.1)
        else:raise AssertionError('server startup failed')
        yield url
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill()


@pytest.fixture
def browser_page(browser_server):
    with sync_playwright() as p:
        executable=p.chromium.executable_path
        if not Path(executable).exists():
            if Path('/usr/bin/chromium').exists():executable='/usr/bin/chromium'
            else:pytest.skip('Install Chromium: uv run playwright install chromium')
        browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1000})
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(browser_server)
        page.wait_for_selector('#loading-overlay',state='hidden')
        yield page
        assert not errors,errors
        browser.close()


def select_demo(page):
    demo=page.locator('#workspace-select option').filter(has_text='데모').get_attribute('value')
    with page.expect_response(lambda r:r.url.endswith('/api/analyses') and r.request.method=='POST'):
        page.select_option('#workspace-select',demo)
    expect(page.locator('#metric-total')).to_contain_text('2,266')
    expect(page.locator('#daily-chart .scatterlayer .trace').first).to_be_attached()


def test_dashboard_charts_filters_heat_keyboard_and_pages(browser_page):
    page=browser_page
    select_demo(page)
    assert page.locator('#daily-chart svg.main-svg').first.bounding_box()['width']>500
    expect(page.locator('.heat-cell')).to_have_count(168)
    page.locator('.heat-cell').first.focus();page.keyboard.press('ArrowRight')
    assert page.locator('.heat-cell').nth(1).evaluate('(el)=>el===document.activeElement')
    page.locator('[data-heat="table"]').click();expect(page.locator('#heat-table')).to_be_visible()
    page.locator('[name="content"]').select_option('photo');expect(page.locator('#dirty-indicator')).to_be_visible()
    assert '2,266' in page.locator('#metric-total').inner_text()
    page.locator('#filters button[type="submit"]').click();expect(page.locator('#dirty-indicator')).to_be_hidden()
    assert '2,266' not in page.locator('#metric-total').inner_text()
    page.locator('[data-page="records"]').click();expect(page.locator('#records-body tr')).to_have_count(50)
    page.locator('#records-next').click();expect(page.locator('#records-position')).to_contain_text('51–100')
    page.locator('[data-page="comparison"]').click();page.locator('#comparison-form button').first.click()
    expect(page.locator('#comparison-result .comparison-metrics > div')).to_have_count(3)
    expect(page.locator('#comparison-result')).to_contain_text('기준 기간')


def test_import_export_and_offline_report(browser_page,sample,tmp_path):
    page=browser_page
    page.locator('#new-workspace').click();page.locator('#workspace-form input').fill('브라우저 검증');page.locator('#workspace-form button').click()
    expect(page.locator('#empty-state')).to_be_visible()
    page.locator('#empty-state [data-import]').click()
    page.locator('#json-file').set_input_files({'name':'result.json','mimeType':'application/json','buffer':json.dumps(sample).encode()})
    expect(page.locator('#import-preview')).to_be_visible()
    page.locator('#import-options summary').click()
    page.locator('#source-timezone').select_option('Asia/Seoul');page.locator('#accept-partial').check();page.locator('#import-submit').click()
    expect(page.locator('#import-dialog')).not_to_be_visible(timeout=20000)
    page.locator('[data-page="dashboard"]').click();page.locator('#filter-start').fill('2026-11-02');page.locator('#filter-end').fill('2026-11-02');page.locator('#filters button[type="submit"]').click()
    expect(page.locator('#metric-total')).to_contain_text('3건')
    expect(page.locator('#coverage-status')).to_have_text('내보내기 범위 미확인')
    assert page.evaluate('typeof window.bad')=='undefined'
    page.locator('#header-export').click()
    with page.expect_download(timeout=20000) as download:
        page.locator('[data-export="html"]').click()
    report=tmp_path/'report.html';download.value.save_as(report)
    expect(page.locator('[data-export="html"] h3')).to_have_text('분석 보고서')
    requests=[]
    offline=page.context.new_page()
    offline.on('request',lambda r:requests.append(r.url))
    page.context.set_offline(True)
    # The managed browser disallows file:// navigation. Render the exact downloaded
    # bytes on about:blank while offline, with no HTTP server or external requests.
    offline.set_content(report.read_text(),wait_until='load')
    expect(offline.locator('.scatterlayer .trace').first).to_be_attached()
    assert offline.locator('#daily svg.main-svg').first.bounding_box()['width']>400
    assert offline.evaluate('typeof window.bad')=='undefined'
    assert not [r for r in requests if r.startswith(('http:','https:'))]
    assert '3' in offline.locator('.card b').first.inner_text()
    page.context.set_offline(False)


def test_mobile_360px_has_no_page_overflow(browser_page):
    page=browser_page;select_demo(page)
    page.set_viewport_size({'width':360,'height':800});page.wait_for_timeout(250)
    assert page.evaluate('document.body.scrollWidth')==360
    page.locator('#menu-toggle').click();page.locator('[data-page="quality"]').click()
    expect(page.locator('#page-quality')).to_be_visible()
    assert page.evaluate('document.body.scrollWidth')==360
    page.locator('#menu-toggle').click();page.locator('[data-page="settings"]').click()
    expect(page.locator('#page-settings')).to_be_visible()
    assert page.evaluate('document.body.scrollWidth')==360


def test_period_presets_and_filter_reset(browser_page):
    page=browser_page;select_demo(page)
    expect(page.locator('#toast-region .toast')).to_have_count(0)
    expect(page.locator('#apply-filters')).to_be_disabled()
    page.locator('[data-period="7"]').click()
    expect(page.locator('#filter-start')).to_have_value('2026-10-25')
    expect(page.locator('#filter-end')).to_have_value('2026-10-31')
    expect(page.locator('#dirty-indicator')).to_be_visible()
    expect(page.locator('#metric-total')).to_contain_text('2,266')
    page.locator('#apply-filters').click()
    expect(page.locator('#metric-days')).to_have_text('7일')
    expect(page.locator('#apply-filters')).to_be_disabled()
    page.locator('#reset-filters').click()
    expect(page.locator('#filter-start')).to_have_value('2026-09-01')
    page.locator('#apply-filters').click()
    expect(page.locator('#metric-total')).to_have_text('4,284건')


def test_import_cancel_keeps_workspace_and_validation_is_inline(browser_page,sample):
    page=browser_page;select_demo(page)
    workspaces=page.locator('#workspace-select option').count()
    page.locator('.heading-actions [data-import]').click()
    expect(page.locator('#import-dialog')).to_be_visible()
    page.locator('#import-dialog .close-dialog').click()
    expect(page.locator('#workspace-select option')).to_have_count(workspaces)
    expect(page.locator('#metric-total')).to_have_text('2,266건')
    page.locator('.heading-actions [data-import]').click()
    page.locator('#json-file').set_input_files({'name':'result.html','mimeType':'text/html','buffer':b'<html>'})
    expect(page.locator('#import-feedback')).to_have_text('JSON 파일을 선택하세요.')
    expect(page.locator('#import-submit')).to_be_disabled()
    page.locator('#json-file').set_input_files({'name':'result.json','mimeType':'application/json','buffer':json.dumps(sample).encode()})
    expect(page.locator('#import-preview')).to_be_visible()
    expect(page.locator('#import-feedback')).to_be_hidden()
    expect(page.locator('#import-submit')).to_be_disabled()
    expect(page.locator('#workspace-select option')).to_have_count(workspaces)
    page.locator('#accept-partial').check()
    expect(page.locator('#import-submit')).to_be_enabled()
    page.locator('#import-dialog .close-dialog').click()


def test_heatmap_table_preserves_rate_unit(browser_page):
    page=browser_page;select_demo(page)
    page.locator('[data-heat="rate"]').click()
    page.locator('[data-heat="table"]').click()
    expect(page.locator('#heat-table caption')).to_have_text('시간당 게시율 (건/시간)')
    expect(page.locator('[data-heat="rate"]')).to_have_attribute('aria-pressed','true')
    page.locator('[data-heat="count"]').click()
    expect(page.locator('#heat-table caption')).to_have_text('관측 게시량 (건)')
    page.locator('[data-heat="table"]').click()
    expect(page.locator('#heat-table')).to_be_hidden()
    expect(page.locator('#heatmap')).to_be_visible()


def test_mobile_menu_backdrop_and_escape(browser_page):
    page=browser_page;select_demo(page)
    page.set_viewport_size({'width':360,'height':800})
    page.locator('#menu-toggle').click()
    expect(page.locator('#menu-toggle')).to_have_attribute('aria-expanded','true')
    page.mouse.click(330,350)
    expect(page.locator('#menu-backdrop')).to_be_hidden()
    page.locator('#menu-toggle').click();page.keyboard.press('Escape')
    expect(page.locator('#menu-toggle')).to_have_attribute('aria-expanded','false')
    assert page.locator('#menu-toggle').evaluate('(el)=>el===document.activeElement')


def test_late_analysis_does_not_replace_new_workspace(browser_page):
    page=browser_page;select_demo(page)
    held=[]
    def hold_analysis(route):
        held.append((route,route.fetch()))
    page.route('**/api/analyses',hold_analysis)
    page.locator('[name="content"]').select_option('photo')
    page.locator('#apply-filters').click()
    expect(page.locator('#page-dashboard')).to_have_attribute('aria-busy','true')
    expect(page.locator('#loading-overlay')).to_be_hidden()
    page.locator('#new-workspace').click()
    page.locator('#workspace-form input').fill('새 빈 작업공간')
    page.locator('#workspace-form button').click()
    expect(page.locator('#empty-state')).to_be_visible()
    assert held
    for route,response in held:route.fulfill(response=response)
    page.unroute('**/api/analyses',hold_analysis)
    expect(page.locator('#workspace-name')).to_have_text('새 빈 작업공간')
    expect(page.locator('#header-export')).to_be_disabled()
    expect(page.locator('#record-badge')).to_have_text('0')
