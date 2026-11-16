"""Offline-render smoke using explicitly synthetic contract fixtures, not HTTP/CSP proof.

Requires Playwright + Chromium. No network, account or market data is used.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_research_robustness import report, fixture, CANDIDATES, SETTINGS, evaluate
from trading_bot.walk_forward_research import run_research


def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    html=(ROOT/'apps/dashboard/research.html').read_text()
    html=re.sub(r'<link[^>]+>|<script[^>]+></script>','',html)
    css=(ROOT/'apps/dashboard/research.css').read_text()
    js=(ROOT/'apps/dashboard/research.mjs').read_text()
    data=report()
    legacy=run_research(fixture(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,evaluator=evaluate)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which("chromium") or None)
        for name,width,scheme in [('desktop',1440,'light'),('mobile',390,'light'),('dark',1440,'dark')]:
            page=browser.new_page(viewport={'width':width,'height':1000},color_scheme=scheme)
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(html);page.add_style_tag(content=css)
            page.evaluate('(data)=>{window.report=data;window.fail=false;window.fetch=async()=>{if(window.fail)throw Error("offline smoke");return {ok:true,json:async()=>window.report}}}',data)
            page.add_script_tag(type='module',content=js)
            page.wait_for_function('document.querySelectorAll("#robustness-cards article").length===3')
            assert page.locator('#research-data').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('.cost-details summary').first.click()
            assert page.locator('.cost-details').first.get_attribute('open') is not None
            page.evaluate('document.querySelector("#research-status").textContent="합성 테스트 데이터 · 실제 투자 성과 아님"')
            page.screenshot(path=str(args.output/f'robustness-{name}.png'),full_page=True)
            page.evaluate('window.fail=true');page.locator('#research-refresh').click()
            page.wait_for_function('document.querySelector("#research-status").textContent.includes("갱신 실패")')
            assert page.locator('#robustness-cards article').count()==3
            page.evaluate('window.fail=false');page.locator('#research-refresh').click()
            page.wait_for_function('document.querySelector("#research-notice").hidden')
            page.evaluate('(r)=>{window.report=r}',legacy);page.locator('#research-refresh').click()
            page.wait_for_function('document.querySelectorAll("#robustness-cards article").length===0')
            assert '--robustness' in page.locator('#robustness-note').inner_text()
            assert not errors,errors
            print(json.dumps({'viewport':name,'checks':'passed','mode':'synthetic_offline_render'}))
            page.close()
        browser.close()

if __name__=='__main__':main()
