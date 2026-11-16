"""Optional Chromium smoke test. Requires `pip install playwright` and Chromium.

Exercises actual demo -> SQLite -> HTTP -> browser without any external request.
"""
import argparse
import importlib.util
import json
import re
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--offline-render', action='store_true', help='Render local assets and SQLite snapshots without browser HTTP; not an end-to-end network test')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright, expect
    spec = importlib.util.spec_from_file_location('smoke_dashboard', ROOT / 'apps/dashboard/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'demo.sqlite'
        subprocess.run([sys.executable, str(ROOT / 'tools/paper_operations_demo.py'), '--journal', str(path)], check=True, capture_output=True)
        with module.create_server(path, 0) as server:
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                    page = browser.new_page(viewport={'width':1440, 'height':1100}, color_scheme='light')
                    errors = []
                    page.on('pageerror', lambda err: errors.append(str(err)))
                    if args.offline_render:
                        def snapshot(url):
                            query = parse_qs(urlsplit(url).query)
                            return module.read_snapshot(path, limit=int(query.get('limit',['100'])[0]),
                                offset=int(query.get('offset',['0'])[0]), status=query.get('status',[None])[0])
                        page.expose_function('localSnapshot', snapshot)
                        html = (ROOT / 'apps/dashboard/index.html').read_text()
                        html = re.sub(r'<link[^>]+>', '', html)
                        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
                        page.set_content(html)
                        page.add_style_tag(content=(ROOT / 'apps/dashboard/styles.css').read_text())
                        page.evaluate("""() => {window.fetch = async url => {
                            if (window.simulateOffline) return new Response('{}', {status:503});
                            return new Response(JSON.stringify(await window.localSnapshot(String(url))),
                                {status:200,headers:{'Content-Type':'application/json'}});
                        };}""")
                        page.add_script_tag(type='module', content=(ROOT / 'apps/dashboard/app.mjs').read_text())
                    else:
                        page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
                    expect(page.locator('#connection')).to_have_text('원장 연결됨')
                    expect(page.locator('#source')).to_contain_text('합성 데이터')
                    expect(page.locator('#total-orders')).to_have_text('6')
                    expect(page.locator('#reconcile-count')).to_have_text('2')
                    expect(page.locator('#order-rows tr')).to_have_count(6)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / 'paper-dashboard-desktop.png'), full_page=True)
                    page.locator('#status-filter').select_option('unknown')
                    expect(page.locator('#order-rows tr')).to_have_count(1)
                    expect(page.locator('#order-rows')).to_contain_text('demo-003')
                    if args.offline_render:
                        page.evaluate('window.simulateOffline = true')
                    else:
                        page.route('**/api/snapshot?**', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"journal_unavailable"}'))
                    page.locator('#refresh').click()
                    expect(page.locator('#connection')).to_have_text('갱신 중단')
                    expect(page.locator('#order-rows tr')).to_have_count(1)
                    expect(page.locator('#connection-notice')).to_contain_text('마지막 성공')
                    if args.offline_render:
                        page.evaluate('window.simulateOffline = false')
                    else:
                        page.unroute('**/api/snapshot?**')
                    page.locator('#status-filter').select_option('')
                    expect(page.locator('#order-rows tr')).to_have_count(6)
                    expect(page.locator('#connection')).to_have_text('원장 연결됨')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    if args.screenshots:
                        page.screenshot(path=str(args.screenshots / 'paper-dashboard-mobile.png'), full_page=True)
                    page.emulate_media(color_scheme='dark')
                    if args.screenshots:
                        page.screenshot(path=str(args.screenshots / 'paper-dashboard-dark.png'), full_page=True)
                    assert not errors, errors
                    browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)
    print(json.dumps({'desktop':True,'mobile':True,'dark_mode':True,'status_filter':True,
                      'stale_data_retained':True,'reconnect':True,'page_errors':0,
                      'browser_http_exercised':not args.offline_render}))


if __name__ == '__main__':
    main()
