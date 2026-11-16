"""Offline-render smoke tests using synthetic results; not browser/HTTP integration."""
import sys,json,re,argparse,shutil
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/server'))
sys.path.insert(0,str(ROOT/'tests'))
from test_quant_research import dataset,CANDIDATES,SETTINGS,metrics
from trading_bot.walk_forward_research import run_research
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();root=ROOT/'apps/dashboard';out=args.output;out.mkdir(parents=True,exist_ok=True)
data=dataset();start=int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000)
for row in data['candles']:row[0]+=start
calls=[0]
def evaluator(candidate,warmup,bars,options):
    calls[0]+=1
    return metrics('-1.25' if calls[0]%3==0 else ('1.50' if candidate['id']=='a' else '0.75'))
report=run_research(data,'a'*64,candidates=CANDIDATES,settings=SETTINGS,evaluator=evaluator)
html=re.sub(r'<link[^>]+>|<script[^>]+></script>','',root.joinpath('research.html').read_text())
js=root.joinpath('research.mjs').read_text().replace('export function','function')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
    for label,width,dark in [('desktop',1440,False),('mobile',390,False),('dark',1440,True)]:
        page=browser.new_page(viewport={'width':width,'height':1000},color_scheme='dark' if dark else 'light')
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/*',lambda route:route.abort())
        page.set_content(html)
        page.add_style_tag(content=root.joinpath('research.css').read_text())
        page.evaluate('(data)=>{window.__research=data;window.__fail=false;window.fetch=async()=>({ok:!window.__fail,json:async()=>structuredClone(window.__research)})}',report)
        page.add_script_tag(content=js)
        page.wait_for_function("document.querySelector('#folds').children.length===3")
        assert page.locator('#mean').inner_text()=='-1.25%'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),label+' overflow'
        if width<480:
            box=page.locator('#folds tr td').nth(2).bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=width, 'mobile result hidden outside viewport'
        page.locator('details summary').first.click()
        page.evaluate("document.querySelector('#source').textContent='합성 UI 검증 데이터 · 실제 거래·수익 아님 | '+document.querySelector('#source').textContent")
        page.screenshot(path=str(out/f'research-{label}.png'),full_page=True)
        previous=page.locator('#mean').inner_text()
        page.evaluate('window.__fail=true');page.locator('#research-refresh').click()
        page.wait_for_function("document.querySelector('#research-data').classList.contains('stale')")
        assert page.locator('#mean').inner_text()==previous
        page.evaluate('window.__fail=false');page.locator('#research-refresh').click()
        page.wait_for_function("!document.querySelector('#research-data').classList.contains('stale')")
        assert not errors,errors
        print(label,'layout, training detail, stale retention and recovery: PASS')
        page.close()
    browser.close()
