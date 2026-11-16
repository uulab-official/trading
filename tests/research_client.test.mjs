import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const path=new URL('../apps/dashboard/research.mjs',import.meta.url);
async function module(){assert.ok(existsSync(path),'research client missing');return import(path);}
function fixture(){return {schema_version:1,kind:'uulab_walk_forward',mode:'paper',live_enabled:false,read_only:true,
  run_id:'a'.repeat(64),generated_at:'2026-01-01T00:00:00+00:00',
  experiment:{symbol:'BTC/USDT',timeframe:'1h',first_bar_ms:0,bars:10,fixture_sha256:'b'.repeat(64),
    settings:{train_size:4,test_size:4,gap:1,warmup:3,min_training_fills:2,initial_balance:'10000',fee_pct:'0.1',slippage_pct:'0.05'},
    candidates:[{id:'a',strategy:'ma_cross',fast_period:1,slow_period:2}]},
  summary:{total_folds:1,evaluated_folds:1,skipped_folds:0,positive_folds:0,mean_test_return_pct:'-1',worst_test_return_pct:'-1',best_test_return_pct:'-1',unused_tail_bars:1},
  folds:[{train_start:0,train_end:4,test_start:5,test_end:9,selected:'a',
    train_scores:[{candidate_id:'a',metrics:{return_pct:'1',fills:2,fees:'1',equity:'10100',cash:'10100',position_value:'0',daily_loss_halted:false,drawdown_halted:false}}],
    test:{return_pct:'-1',fills:2,fees:'1',equity:'9900',cash:'9900',position_value:'0',daily_loss_halted:false,drawdown_halted:false}}]};}
test('validate read-only research including losses',async()=>{const m=await module();assert.equal(m.validateResearch(fixture()).summary.mean_test_return_pct,'-1')});
test('reject live, bad numbers, mismatched selection and fold bounds',async()=>{const m=await module();
  for(const mutate of [r=>r.mode='live',r=>r.summary.mean_test_return_pct='NaN',r=>r.folds[0].selected='missing',r=>r.folds[0].test_end=0,r=>r.summary.total_folds=99]){
    const r=fixture();mutate(r);assert.throws(()=>m.validateResearch(r));}});
test('display precision is not converted through floats',async()=>{const m=await module();assert.equal(m.formatAmount('-1234567890.00000001'),'-1,234,567,890.00000001');assert.equal(m.formatAmount('NaN'),'—')});
test('failed request retains last good report and recovers',async()=>{const m=await module();let fail=false,seen=[],errors=[];const c=m.createController({request:async()=>{if(fail)throw Error('down');return fixture()},onData:r=>seen.push(r),onError:(e,r)=>errors.push(r)});
  await c.refresh();fail=true;await c.refresh();assert.equal(errors[0],seen[0]);fail=false;await c.refresh();assert.equal(seen.length,2)});
test('refresh requests cannot overlap',async()=>{const m=await module();let resolve,calls=0;const c=m.createController({request:()=>{calls++;return new Promise(r=>resolve=r)},onData:()=>{},onError:()=>{}});
  const a=c.refresh(),b=c.refresh();assert.equal(a,b);await Promise.resolve();resolve(fixture());await a;assert.equal(calls,1)});
test('invalid refresh cannot replace last good data',async()=>{const m=await module();let current=fixture(),good,last;const c=m.createController({request:async()=>current,onData:r=>good=r,onError:(e,r)=>last=r});await c.refresh();current={mode:'live'};await c.refresh();assert.equal(last,good)});
