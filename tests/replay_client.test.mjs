import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync} from 'node:fs';
const path=new URL('../apps/dashboard/replay.mjs',import.meta.url);
let m=null;
if(existsSync(path)) m=await import(path);
const snapshot=()=>({schema_version:1,kind:'uulab_paper_replay',mode:'paper',read_only:true,live_enabled:false,
 source:'local_fixture',status:'fixture_complete',run_id:'a'.repeat(64),fixture_sha256:'b'.repeat(64),
 generated_at:'2026-10-31T10:00:00+00:00',first_bar_ms:0,last_bar_ms:7200000,bars_processed:3,
 mark_price:'110',configuration:{symbol:'BTC/USDT',timeframe:'1h',strategy:'ma_cross',fee_pct:'1',slippage_pct:'0'},
 account:{currency:'USDT',initial_balance:'1000',cash:'1007.90',equity:'1007.90',total_fees:'2.10',total_pnl:'7.90',total_return_pct:'0.79'},
 positions:[],fill_count:0,fills:[],daily_loss_halted:false,drawdown_halted:false});
const ready=()=>assert.ok(m,'replay UI module is missing');

test('offline contract and signed Decimal display',()=>{
 ready(); assert.equal(m.validateReplay(snapshot()).source,'local_fixture');
 assert.equal(m.formatDecimal('-1234.00000001'),'-1,234.00000001');
 assert.equal(m.formatDecimal('1000000000000000000.01'),'1,000,000,000,000,000,000.01');
 assert.equal(m.formatDecimal('NaN'),'—');
});
test('reject live, nonfinite and malformed monetary state',()=>{
 ready(); for(const edit of [s=>s.live_enabled=true,s=>s.mode='live',s=>s.account.cash='NaN',
 s=>s.account.total_fees='-1',s=>s.account.currency='EUR',s=>s.generated_at='bad',s=>s.bars_processed=true]){
  const s=snapshot();edit(s);assert.throws(()=>m.validateReplay(s));
 }
});
test('reject mismatched fill symbol/currency and oversized responses',()=>{
 ready();const s=snapshot();s.fill_count=1;s.fills=[{id:'x',client_id:'x',side:'buy',symbol:'ETH/USDT',
 quantity:'1',price:'100',fee:'1',fee_currency:'USDT',timestamp_ms:3600000}];
 assert.throws(()=>m.validateReplay(s));s.fills[0].symbol='BTC/USDT';
 assert.equal(m.validateReplay(s).fills.length,1);s.fills[0].fee_currency='KRW';assert.throws(()=>m.validateReplay(s));
});
test('concurrent refresh requests are shared',async()=>{
 ready();let calls=0,resolve;const gate=new Promise(r=>{resolve=r});
 const c=m.createReplayController({request:()=>{calls++;return gate},onData:()=>{},onError:()=>{}});
 const a=c.refresh(),b=c.refresh();assert.equal(a,b);resolve(snapshot());await a;assert.equal(calls,1);
});
test('failed or invalid refresh retains last validated result',async()=>{
 ready();let next=snapshot(),last=null,errorPrevious;
 const c=m.createReplayController({request:async()=>next,onData:data=>{last=data},onError:(_e,p)=>{errorPrevious=p}});
 await c.refresh();next={...snapshot(),mode:'live'};await c.refresh();assert.equal(errorPrevious,last);
 next=snapshot();await c.refresh();assert.equal(last.mode,'paper');
});
test('timestamps are bounded and generation timezone is explicit',()=>{
 ready();const s=snapshot();s.last_bar_ms=-1;assert.throws(()=>m.validateReplay(s));
 s.last_bar_ms=7200000;s.generated_at='2026-10-31T10:00:00';assert.throws(()=>m.validateReplay(s));
});

test('reject responses exceeding the bounded fill page',()=>{ready();const s=snapshot();s.fill_count=101;s.fills=Array.from({length:101},(_,i)=>({id:String(i),client_id:String(i),symbol:'BTC/USDT',side:'buy',quantity:'1',price:'1',fee:'0',fee_currency:'USDT',timestamp_ms:3600000}));assert.throws(()=>m.validateReplay(s));});
