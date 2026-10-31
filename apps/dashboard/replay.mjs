// Independent read-only view: executed offline replay is NOT reservation accounting.
const amount=(v,signed=false)=>typeof v==='string' && v.length<=100 &&
  /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v) &&
  Number.isFinite(Number(v)) && (signed || Number(v)>=0);
const integer=v=>Number.isSafeInteger(v) && v>=0;
const stamp=v=>integer(v) && v<=253402300799999;
const text=v=>typeof v==='string' && v.length>0 && v.length<=128;
const digest=v=>typeof v==='string' && /^[0-9a-f]{64}$/.test(v);

export function formatDecimal(value){
  if(!amount(value,true)) return '—';
  if(/[eE]/.test(value)) return value;
  const [head,tail]=value.split('.');
  return head.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(tail ? '.'+tail : '');
}
export function validateReplay(v){
  if(!v || v.schema_version!==1 || v.kind!=='uulab_paper_replay' || v.mode!=='paper' ||
     v.live_enabled!==false || v.read_only!==true || v.source!=='local_fixture' ||
     v.status!=='fixture_complete' || !digest(v.run_id) || !digest(v.fixture_sha256) ||
     typeof v.generated_at!=='string' || !/(Z|[+-]\d\d:\d\d)$/.test(v.generated_at) ||
     !Number.isFinite(Date.parse(v.generated_at)) || !stamp(v.first_bar_ms) ||
     !stamp(v.last_bar_ms) || v.last_bar_ms<v.first_bar_ms || !integer(v.bars_processed) || !v.bars_processed ||
     !integer(v.fill_count) || typeof v.daily_loss_halted!=='boolean' || typeof v.drawdown_halted!=='boolean')
    throw new Error('invalid replay');
  const a=v.account,c=v.configuration;
  if(!a || !['USDT','KRW'].includes(a.currency) || !c || !text(c.symbol) ||
     !c.symbol.endsWith('/'+a.currency) || !['ma_cross','rsi'].includes(c.strategy) || !text(c.timeframe) ||
     !['initial_balance','cash','equity','total_fees'].every(k=>amount(a[k])) ||
     !['total_pnl','total_return_pct'].every(k=>amount(a[k],true)) ||
     !amount(c.fee_pct) || !amount(c.slippage_pct) || !amount(v.mark_price) ||
     !Array.isArray(v.positions) || v.positions.length>1 || !Array.isArray(v.fills) ||
     v.fills.length>100 || v.fills.length>v.fill_count) throw new Error('invalid replay account');
  for(const p of v.positions)
    if(!text(p.asset) || p.asset!==c.symbol.split('/')[0] || p.currency!==a.currency ||
       !['quantity','mark_price','market_value'].every(k=>amount(p[k]))) throw new Error('invalid position');
  const ids=new Set(),keys=new Set();let previous=v.first_bar_ms;
  for(const f of v.fills){
    if(!text(f.id) || !text(f.client_id) || ids.has(f.id) || keys.has(f.client_id) ||
       f.symbol!==c.symbol || f.fee_currency!==a.currency || !['buy','sell'].includes(f.side) ||
       !['quantity','price','fee'].every(k=>amount(f[k])) || !stamp(f.timestamp_ms) ||
       f.timestamp_ms<previous || f.timestamp_ms>v.last_bar_ms) throw new Error('invalid replay fill');
    ids.add(f.id);keys.add(f.client_id);previous=f.timestamp_ms;
  }
  return v;
}
export function createReplayController({request,onData,onError}){
  let pending=null,lastGood=null;
  return {refresh(){
    if(pending) return pending;
    pending=Promise.resolve().then(request).then(validateReplay).then(data=>{
      onData(data);lastGood=data;
    }).catch(error=>onError(error,lastGood)).finally(()=>{pending=null});
    return pending;
  }};
}

function boot(){
  const $=id=>document.getElementById(id);
  if(!$('replay-panel')) return;
  let timer=null,busy=false;
  const time=value=>new Intl.DateTimeFormat('ko-KR',{dateStyle:'medium',timeStyle:'medium',timeZone:'Asia/Seoul'}).format(new Date(value));
  const cell=(tag,value)=>{const el=document.createElement(tag);el.textContent=String(value);return el;};
  const money=(value,currency)=>formatDecimal(value)+' '+currency;
  function render(data){
    const a=data.account,c=data.configuration;
    $('replay-data').hidden=false;$('replay-data').classList.remove('stale');
    $('replay-status').textContent='오프라인 재생 완료 · 실시간 계좌 아님';
    $('replay-notice').textContent='로컬 입력 캔들을 기존 PaperRunner로 재생한 결과입니다. 주문 예약 원장과 합산하지 않습니다. 수익성이나 실거래 성과를 입증하지 않습니다.';
    $('replay-equity').textContent=money(a.equity,a.currency);
    $('replay-cash').textContent=money(a.cash,a.currency);
    $('replay-pnl').textContent=money(a.total_pnl,a.currency);
    $('replay-return').textContent='누적 수익률 '+formatDecimal(a.total_return_pct)+'% · 비용 반영';
    $('replay-fees').textContent=money(a.total_fees,a.currency);
    $('replay-details').textContent=`${c.symbol} · ${c.timeframe} · ${c.strategy} · ${data.bars_processed}개 봉 · 초기 자금 ${money(a.initial_balance,a.currency)} · 수수료 ${c.fee_pct}% / 슬리피지 ${c.slippage_pct}%`;
    $('replay-time').textContent=`최종 평가 봉의 시작 ${time(data.last_bar_ms)} KST · 보고서 생성 ${time(data.generated_at)} KST`;
    $('replay-risk').textContent=`재생 종료 시 위험 차단: 일일 손실 ${data.daily_loss_halted?'발동':'미발동'} / 낙폭 ${data.drawdown_halted?'발동':'미발동'} · 현재 실거래 상태와 무관`;
    $('replay-provenance').textContent='입력 SHA-256 '+data.fixture_sha256+' · 실행 ID '+data.run_id;
    $('replay-positions').textContent=data.positions.length
      ? data.positions.map(p=>`${p.asset} ${formatDecimal(p.quantity)} · 종가 평가 ${money(p.market_value,p.currency)}`).join(' / ')
      : '재생 종료 시 보유 자산 없음';
    $('replay-fill-count').textContent=`전체 ${data.fill_count}건 중 최근 ${data.fills.length}건 · 모의 체결`;
    const rows=$('replay-fills');rows.replaceChildren();
    for(const f of [...data.fills].reverse()){
      const row=document.createElement('tr');
      row.append(cell('td',f.client_id),cell('td',f.side==='buy'?'모의 매수':'모의 매도'),
        cell('td',formatDecimal(f.quantity)),cell('td',money(f.price,a.currency)),
        cell('td',money(f.fee,a.currency)),cell('td',time(f.timestamp_ms)));
      rows.append(row);
    }
    $('replay-no-fills').hidden=data.fills.length>0;
    $('replay-last-fetch').textContent='조회 성공 '+time(Date.now())+' KST · 시세 갱신이 아닙니다.';
  }
  function failure(_error,last){
    $('replay-status').textContent=last?'보고서 갱신 실패':'재생 보고서 미연결';
    $('replay-notice').textContent=last
      ? '마지막 정상 보고서를 유지하고 있습니다. 파일이나 서버 연결을 확인하세요. 아래 결과는 새로 확인된 정보가 아닙니다.'
      : '검증된 재생 보고서를 읽을 수 없습니다. export 명령으로 생성한 파일을 --replay-report로 지정하세요. 잔고 0 또는 무거래를 뜻하지 않습니다.';
    $('replay-data').classList.add('stale');
  }
  async function request(){
    const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),5000);
    try{
      const response=await fetch('/api/replay',{cache:'no-store',credentials:'omit',signal:abort.signal});
      if(!response.ok) throw new Error('report unavailable');
      return await response.json();
    }finally{clearTimeout(timeout);}
  }
  const controller=createReplayController({request,onData:render,onError:failure});
  async function refresh(){
    if(busy) return;
    busy=true;clearTimeout(timer);$('replay-refresh').disabled=true;
    try{await controller.refresh()}finally{
      busy=false;$('replay-refresh').disabled=false;
      timer=setTimeout(()=>{if(!document.hidden) refresh()},15000);
    }
  }
  $('replay-refresh').addEventListener('click',refresh);
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden) refresh()});
  refresh();
}
if(typeof document!=='undefined') boot();
