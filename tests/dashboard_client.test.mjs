import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const url = new URL('../apps/dashboard/app.mjs', import.meta.url);
let client = null;
if (existsSync(url)) client = await import(url);
const fixture = () => ({mode:'paper', live_enabled:false, read_only:true,
  journal_connected:true, accounting_available:false, source:'paper_journal',
  as_of:'2026-10-31T10:00:00+00:00', summary:{total_orders:1,active_orders:1,
  needs_reconciliation:1,reserved_by_currency:{KRW:'100.000000000000000001'},
  status_counts:{unknown:1},last_order_update:'2026-10-31 10:00:00'},
  orders:[{client_id:'<img src=x onerror=alert(1)>',status:'unknown',reserved:'100',currency:'KRW',symbol:'TEST',updated_at:'2026-10-31 10:00:00'}],
  pagination:{limit:25,offset:0,total:1,has_more:false},audit_available:false,events:[]});
function loaded(){ assert.ok(client, 'dashboard client module must exist'); return client; }
test('valid snapshot preserves exact decimal text and untrusted IDs as text', () => {
  const data=loaded().validateSnapshot(fixture());
  assert.equal(data.summary.reserved_by_currency.KRW,'100.000000000000000001');
  assert.match(data.orders[0].client_id, /<img/);
});
test('invalid/live data is not displayed as paper state', () => {
  loaded();
  for(const change of [{mode:'live'},{live_enabled:true},{read_only:false},{orders:{}},{source:'real_broker'}])
    assert.throws(() => loaded().validateSnapshot({...fixture(),...change}));
});
test('malformed statuses and amounts rejected', () => {
  loaded();
  for(const change of [{status:'made_up'},{reserved:'NaN'},{reserved:'-1'},{currency:'<bad>'}]) {
    const data=fixture(); data.orders[0]={...data.orders[0],...change};
    assert.throws(() => loaded().validateSnapshot(data));
  }
});
test('inconsistent summary and pagination rejected', () => {
  loaded();
  const data=fixture(); data.summary.active_orders=3;
  assert.throws(() => loaded().validateSnapshot(data));
  data.summary.active_orders=1; data.pagination.offset=-1;
  assert.throws(() => loaded().validateSnapshot(data));
});
test('legacy timestamps are interpreted as UTC not local timezone', () => {
  assert.equal(loaded().toUTCDate('2026-10-31 10:20:30').toISOString(),'2026-10-31T10:20:30.000Z');
  assert.equal(loaded().toUTCDate('2026-10-31T19:20:30+09:00').toISOString(),'2026-10-31T10:20:30.000Z');
  assert.equal(loaded().toUTCDate('not a date'),null);
});
test('amount display never rounds through floating point', () => {
  assert.equal(loaded().formatAmount('1000.000000000000000001'),'1,000.000000000000000001');
});
test('simultaneous refreshes never overlap network work', async () => {
  const {createRefreshController}=loaded(); let calls=0, release;
  const waiting=new Promise(resolve => {release=resolve;});
  const ctrl=createRefreshController({request:async()=>{calls++; await waiting; return fixture();},onData:()=>{},onError:()=>{}});
  const a=ctrl.refresh(), b=ctrl.refresh();
  release(); await Promise.all([a,b]); assert.equal(calls,1);
});
test('a failed refresh retains last good data', async () => {
  const {createRefreshController}=loaded(); let fail=false, stale=null, rendered=0;
  const ctrl=createRefreshController({request:async()=>{if(fail)throw new Error('offline'); return fixture();},
    onData:()=>rendered++,onError:(_err,previous)=>{stale=previous;}});
  await ctrl.refresh(); fail=true; await ctrl.refresh();
  assert.equal(rendered,1); assert.equal(stale.orders[0].status,'unknown');
});
test('invalid response cannot replace last good data', async () => {
  const {createRefreshController}=loaded(); let value=fixture(), kept=null;
  const ctrl=createRefreshController({request:async()=>value,onData:()=>{},onError:(_err,previous)=>kept=previous});
  await ctrl.refresh(); value={mode:'paper'}; await ctrl.refresh();
  assert.equal(kept.summary.total_orders,1);
});
