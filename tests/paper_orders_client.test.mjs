import test from 'node:test';
import assert from 'node:assert/strict';
const mod = await import('../apps/dashboard/paper-orders.mjs').catch(() => null);
const api = () => { assert.ok(mod, 'order browser module is missing'); return mod; };
const copy = x => JSON.parse(JSON.stringify(x));
function page({offset=0, limit=2, matched=5, status='all', revision='9007199254740999', digest='a'.repeat(64)}={}) {
  const items = Array.from({length:Math.max(0,Math.min(limit,matched-offset))},(_,i)=>({
    client_order_id:`order-${offset+i}`,symbol:'BTC/USDT',side:'buy',order_type:'market',
    amount:'1.000000000000000000001',filled:'0',remaining:'1.000000000000000000001',price:null,
    status:status==='filled'?'filled':'unknown',exchange_order_id:null,
    created_at:'2026-01-01T00:00:00+00:00',updated_at:'2026-01-01T00:00:00+00:00',
    strategy_id:null,cancel_requested:false,account_fill_present:false
  }));
  return {schema_version:1,source:'durable_paper_order_page',mode:'paper',read_only:true,live_enabled:false,
    account_id:'b'.repeat(32),session_version:3,revision,digest,observed_at:'2026-11-03T10:00:00+00:00',
    orders:{total:matched,matched,status,offset,limit,has_previous:offset>0,has_next:offset+limit<matched,items}};
}
function setup() {
  const s=api().createOrderBrowser(); const req=s.begin({reset:true,limit:2});
  s.accept(req.id,page()); return s;
}

test('page validator preserves precise quantities and large revision strings',()=>{
  const p=page(); assert.equal(api().validatePage(p),p); assert.equal(p.orders.items[0].amount,'1.000000000000000000001');
});
test('empty page and empty filtered result are not errors',()=>{
  api().validatePage(page({matched:0})); api().validatePage(page({matched:0,status:'filled'}));
});
test('malformed safety flags counts page lengths and digits fail closed',()=>{
  const mutations=[p=>p.live_enabled=true,p=>p.read_only=false,p=>p.mode='live',p=>p.orders.total=-1,
    p=>p.orders.matched=6,p=>p.orders.items.pop(),p=>p.orders.has_next=false,p=>p.revision=1,
    p=>p.revision='01',p=>p.orders.items[0].amount='NaN',p=>p.orders.items[0].remaining='0',
    p=>p.orders.items[0].amount='1e999',p=>p.orders.items[0].account_fill_present='yes'];
  for (const mutate of mutations) {const p=page();mutate(p);assert.throws(()=>api().validatePage(p));}
});
test('duplicate IDs status mismatch and impossible filled state fail closed',()=>{
  for(const mutate of [p=>p.orders.items[1].client_order_id=p.orders.items[0].client_order_id,
    p=>p.orders.status='filled',p=>p.orders.items[0].status='filled',p=>p.orders.items[0].symbol='\u0000']) {
    const p=page();mutate(p);assert.throws(()=>api().validatePage(p));
  }
});
test('next page URL carries original revision and digest not a path',()=>{
  const s=setup(),req=s.begin({offset:2,limit:2}); const u=new URL(req.url,'http://localhost');
  assert.equal(u.pathname,'/api/paper-session/orders');assert.equal(u.searchParams.get('revision'),'9007199254740999');
  assert.equal(u.searchParams.get('digest'),'a'.repeat(64));assert.equal(u.searchParams.get('offset'),'2');
  assert.equal(u.searchParams.has('path'),false);
});
test('accepting a different continuation revision preserves last good page',()=>{
  const s=setup(),req=s.begin({offset:2,limit:2});
  assert.throws(()=>s.accept(req.id,page({offset:2,revision:'9007199254741000',digest:'c'.repeat(64)})));
  assert.equal(s.view().report.orders.offset,0);assert.equal(s.view().stale,true);
});
test('409 requires explicit reset and never automatically requests again',()=>{
  const s=setup(),req=s.begin({offset:2,limit:2}); s.fail(req.id,'changed');
  assert.equal(s.view().restartRequired,true);assert.equal(s.begin({offset:2,limit:2}),null);
  const restart=s.begin({reset:true,limit:2});assert.equal(new URL(restart.url,'http://x').searchParams.has('revision'),false);
  s.accept(restart.id,page({revision:'9007199254741000',digest:'c'.repeat(64)}));
  assert.equal(s.view().restartRequired,false);assert.equal(s.view().stale,false);
});
test('busy guard prevents duplicate requests',()=>{
  const s=setup();s.begin({offset:2,limit:2});assert.equal(s.begin({reset:true,limit:2}),null);
});
test('late results after timeout cannot overwrite newer explicit reset',()=>{
  const s=setup(),old=s.begin({offset:2,limit:2});s.fail(old.id,'unavailable');
  const req=s.begin({reset:true,limit:2});s.accept(req.id,page({revision:'9007199254741000',digest:'c'.repeat(64)}));
  assert.equal(s.accept(old.id,page({offset:2})),false);assert.equal(s.view().report.revision,'9007199254741000');
});
test('server page must match requested offset filter and page size',()=>{
  for(const mutate of [p=>p.orders.offset=0,p=>p.orders.limit=1,p=>p.orders.status='unknown']) {
    const s=setup(),req=s.begin({offset:2,limit:2}),p=page({offset:2});mutate(p);assert.throws(()=>s.accept(req.id,p));
  }
});
test('a reset cannot silently switch account or accept regressing revision',()=>{
  for(const mutate of [p=>p.account_id='d'.repeat(32),p=>p.revision='1',p=>p.digest='c'.repeat(64)]) {
    const s=setup(),req=s.begin({reset:true,limit:2}),p=page();mutate(p);assert.throws(()=>s.accept(req.id,p));
  }
});
test('filter change starts at zero and does not reuse old page tokens',()=>{
  const s=setup(),req=s.begin({reset:true,status:'filled',limit:2});const u=new URL(req.url,'http://x');
  assert.equal(u.searchParams.get('status'),'filled');assert.equal(u.searchParams.get('offset'),'0');
  s.accept(req.id,page({matched:0,status:'filled'}));assert.equal(s.view().report.orders.status,'filled');
});
test('failed refresh retains data and makes it explicitly stale',()=>{
  const s=setup(),req=s.begin({reset:true,limit:2});s.fail(req.id,'unavailable');
  const v=s.view();assert.equal(v.stale,true);assert.equal(v.busy,false);assert.equal(v.report.orders.total,5);
});
test('returned data and accepted input are detached from state',()=>{
  const s=api().createOrderBrowser(),req=s.begin({reset:true,limit:2}),p=page();s.accept(req.id,p);
  p.orders.items[0].amount='999';let v=s.view();v.report.orders.items=[];
  assert.equal(s.view().report.orders.items[0].amount,'1.000000000000000000001');
});
test('no new query can be built from unbounded values',()=>{
  for(const q of [{reset:true,limit:101},{reset:true,offset:1},{reset:true,status:'</script>'},{reset:true,limit:NaN}])
    assert.throws(()=>api().createOrderBrowser().begin(q));
});

test('impossible calendar dates are not accepted as observation times',()=>{
  for (const stamp of ['2026-02-30T10:00:00+00:00','2026-11-03T24:00:00+00:00','2026-11-03T00:00:61+00:00']) {
    const p=page();p.observed_at=stamp;assert.throws(()=>api().validatePage(p));
  }
});
