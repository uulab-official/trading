import test from 'node:test';
import assert from 'node:assert/strict';
import * as ui from '../apps/dashboard/replay.mjs';
const start=Date.parse('2026-10-01T00:00:00Z');
function report(){return {schema_version:1,kind:'uulab_paper_replay',mode:'paper',live_enabled:false,read_only:true,
 source:'local_fixture',status:'fixture_complete',run_id:'a'.repeat(64),fixture_sha256:'b'.repeat(64),
 generated_at:'2026-10-01T04:00:00Z',first_bar_ms:start,last_bar_ms:start+7200000,bars_processed:3,
 fill_count:0,daily_loss_halted:false,drawdown_halted:false,mark_price:'101',
 configuration:{symbol:'BTC/KRW',timeframe:'1h',strategy:'ma_cross',fee_pct:'0.1',slippage_pct:'0.05'},
 account:{currency:'KRW',initial_balance:'1000',cash:'1000',equity:'1000',total_fees:'0',total_pnl:'0',total_return_pct:'0'},
 positions:[],fills:[]};}
function provenance(){return {schema_version:1,provider:'upbit_public_rest',market:'KRW-BTC',unit_minutes:60,
 end_exclusive_ms:start+10800000,retrieved_at:'2026-10-01T03:10:00Z',bar_count:3,
 candles_sha256:'c'.repeat(64),gap_policy:'reject',missing_intervals:0,authenticated:false};}
test('legacy source stays unknown instead of claiming market provenance',()=>{
 assert.equal(typeof ui.marketDataLabel,'function');assert.match(ui.marketDataLabel(ui.validateReplay(report())),/출처 미확인/);
});
test('validated provider record is labeled as a record, not authentication',()=>{
 const r={...report(),data_provenance:provenance()};ui.validateReplay(r);
 assert.equal(typeof ui.marketDataLabel,'function');assert.match(ui.marketDataLabel(r),/업비트.*KRW-BTC/);
 assert.match(ui.marketDataLabel(r),/인증 아님/);
});
test('tampered provider annotation fails closed',()=>{
 for(const [key,value] of [['provider','other'],['market','KRW-ETH'],['authenticated',true],['unit_minutes',5],
 ['missing_intervals',true],['bar_count',4],['end_exclusive_ms',start],['retrieved_at','bad'],['api_key','secret']]){
   assert.throws(()=>ui.validateReplay({...report(),data_provenance:{...provenance(),[key]:value}}),key);
 }
 assert.throws(()=>ui.validateReplay({...report(),data_provenance:null}));
});
test('invalid source keeps last good replay instead of replacing it',async()=>{
 const good={...report(),data_provenance:provenance()};let input=good,last,shown;
 const control=ui.createReplayController({request:()=>input,onData:v=>{shown=v},onError:(_e,v)=>{last=v}});
 await control.refresh();input={...good,data_provenance:{...provenance(),authenticated:true}};
 await control.refresh();assert.equal(last,good);assert.equal(shown,good);
});
