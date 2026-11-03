import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
const url = new URL('../apps/dashboard/paper-account.mjs', import.meta.url);
async function api(){ assert.ok(existsSync(url), 'paper account module missing'); return import(url); }
function report(){ return {schema_version:1,source:'durable_paper_session',mode:'paper',read_only:true,live_enabled:false,
 account_id:'a'.repeat(32),session_version:3,revision:'9007199254740993',digest:'b'.repeat(64),observed_at:'2026-11-03T01:00:00.000000+00:00',
 control:{enabled:true,mode:'halted',generation:0,reason:'created'},policy:{enabled:false,max_quote_age_ms:null,max_order_notional:null},
 observation:{process_state:'unobserved',order_authorization:'not_assessed',quote_receipts:'unavailable_to_observer',valuation_basis:'saved_marks',missing_marks:[],missing_marks_count:0},
 account:{quote_currency:'USDT',initial_cash:'1000',cash:'1000',total_fees:'0',marked_equity:'1000',position_value:'0',holdings:[],holdings_count:0,holdings_truncated:false},
 orders:{total:0,unresolved:0,confirmed_filled:0,account_fills:0,unconfirmed_account_fills:0,items:[],truncated:false}}; }

test('accepts precise strings and rejects live/non-read-only metadata',async()=>{
 const {validateReport}=await api(); assert.equal(validateReport(report()).revision,'9007199254740993');
 for(const [key,value] of [['live_enabled',true],['read_only',false],['mode','live'],['schema_version',9]]){
  const r=report(); r[key]=value; assert.throws(()=>validateReport(r));
 }
});
test('rejects malformed values and silently corrected impossible dates',async()=>{
 const {validateReport}=await api();
 for(const v of ['NaN','Infinity','-1','1e999',123,null]){const r=report();r.account.cash=v;assert.throws(()=>validateReport(r));}
 for(const v of ['2026-02-30T01:00:00+00:00','not-a-date']){const r=report();r.observed_at=v;assert.throws(()=>validateReport(r));}
});
test('counts and bounded details must agree',async()=>{
 const {validateReport}=await api();
 const r=report();r.orders.unresolved=1;assert.throws(()=>validateReport(r));
 const t=report();t.orders.total=200;t.orders.truncated=false;assert.throws(()=>validateReport(t));
});
test('missing valuation is not turned into zero',async()=>{
 const {validateReport,formatDecimal}=await api(); const r=report();r.account.marked_equity=null;r.account.position_value=null;
 r.observation.missing_marks=['BTC/USDT'];r.observation.missing_marks_count=1;
 assert.equal(validateReport(r).account.marked_equity,null);assert.equal(formatDecimal(null),'미산출');
});
test('legacy control is explicitly absent, not active',async()=>{
 const {controlLabel,validateReport}=await api(); const r=report();r.session_version=1;
 r.control={enabled:false,mode:null,generation:null,reason:null};assert.equal(controlLabel(validateReport(r).control),'제어 미설정');
});
test('mode label never authorizes new orders',async()=>{
 const {controlLabel}=await api();assert.equal(controlLabel({enabled:true,mode:'active'}),'정상 모드 · 저장값');
 assert.equal(controlLabel({enabled:true,mode:'reduce_only'}),'축소 전용 · 저장값');
 assert.equal(controlLabel({enabled:true,mode:'halted'}),'중단 · 저장값');
});
test('formatting never converts precise decimals through binary floating point',async()=>{
 const {formatDecimal}=await api();assert.equal(formatDecimal('9007199254740993.12'),'9,007,199,254,740,993.12');
 assert.equal(formatDecimal('1E-8'),'0.00000001');assert.equal(formatDecimal('1.123456789'),'1.12345678…');
});
test('quote time is preserved and future time is explicitly flagged',async()=>{
 const {ageLabel}=await api();assert.equal(ageLabel(null,1000),'가격 시각 없음');
 assert.match(ageLabel(2000,1000),/미래/);assert.equal(ageLabel(1000,62000),'1분 전');
});
test('last good report survives failure with an explicit stale flag',async()=>{
 const {createMonitorState}=await api();const s=createMonitorState();s.accept(report());s.fail();
 assert.equal(s.view().stale,true);assert.equal(s.view().report.account.cash,'1000');
 s.accept(report());assert.equal(s.view().stale,false);
});
test('account switch, revision rollback and same-revision conflict are rejected',async()=>{
 const {createMonitorState}=await api();const s=createMonitorState();s.accept(report());
 const r=report();r.revision='9007199254740992';assert.throws(()=>s.accept(r));
 const other=report();other.account_id='c'.repeat(32);assert.throws(()=>s.accept(other));
 const changed=report();changed.digest='d'.repeat(64);assert.throws(()=>s.accept(changed));
 assert.equal(s.view().report.digest,'b'.repeat(64));assert.equal(s.view().stale,true);
});
test('state snapshots are detached from input and readers',async()=>{
 const {createMonitorState}=await api();const s=createMonitorState(),r=report();s.accept(r);r.account.cash='99';
 const v=s.view();v.report.account.cash='22';assert.equal(s.view().report.account.cash,'1000');
});
test('UI has no HTML injection or write request path',async()=>{
 await api();const code=readFileSync(url,'utf8');assert.doesNotMatch(code,/\.innerHTML\s*=|insertAdjacentHTML/);
 assert.doesNotMatch(code,/method:\s*['"](?:POST|PUT|DELETE|PATCH)/);
});

test('out-of-range quote dates cannot reach DOM date formatting',async()=>{
 const {validateReport}=await api();const r=report();
 r.account.holdings=[{asset:'BTC',quantity:'1',mark:'100',marked_value:'100',mark_timestamp_ms:9000000000000000}];
 r.account.holdings_count=1;assert.throws(()=>validateReport(r));
});
test('contradictory control and policy metadata is rejected',async()=>{
 const {validateReport}=await api();const r=report();r.control.mode='active';assert.throws(()=>validateReport(r));
 const q=report();q.policy.enabled=true;assert.throws(()=>validateReport(q));
});
