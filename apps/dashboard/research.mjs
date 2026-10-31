// Independent-window research only. No optimization or order requests in browser.
const count=v=>Number.isSafeInteger(v)&&v>=0;
const decimal=(v,signed=false)=>typeof v==='string'&&v.length<=100&&/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(v)&&Number.isFinite(Number(v))&&(signed||Number(v)>=0);
const id=v=>typeof v==='string'&&/^[a-zA-Z0-9_-]{1,48}$/.test(v);
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const metric=m=>m&&['cash','equity','position_value','fees'].every(k=>decimal(m[k]))&&decimal(m.return_pct,true)&&count(m.fills)&&typeof m.daily_loss_halted==='boolean'&&typeof m.drawdown_halted==='boolean';
export function formatAmount(v){if(!decimal(v,true))return '—';if(/[eE]/.test(v))return v;const [h,t]=v.split('.');return h.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(t?'.'+t:'');}

const ROBUSTNESS_POLICY=Object.freeze({selection:'frozen_base_training',allocation:'initial_position_size_pct',
  entry:'second_test_bar_open',valuation:'last_test_bar_close',liquidation:'not_forced',
  benchmark_risk:'no_active_exits',cash:'zero_interest'});
function validateRobustness(r){
  if(r.schema_version===1){if(Object.hasOwn(r,'robustness'))throw Error('unexpected diagnostics');return;}
  const d=r.robustness, e=r.experiment, multipliers=['1','2','3'];
  if(!d||!d.policy||Object.keys(d.policy).length!==8||
     Object.entries(ROBUSTNESS_POLICY).some(([k,v])=>d.policy[k]!==v)||
     JSON.stringify(d.policy.multipliers)!==JSON.stringify(multipliers)||
     !Array.isArray(d.folds)||d.folds.length!==r.folds.length||
     !Array.isArray(d.summary)||d.summary.length!==3)throw Error('invalid diagnostic policy');
  const duration=Number(e.timeframe.slice(0,-1))*{m:60000,h:3600000,d:86400000,w:604800000}[e.timeframe.at(-1)];
  d.folds.forEach((f,i)=>{
    if(!f||f.entry_bar_ms!==e.first_bar_ms+(r.folds[i].test_start+1)*duration||
       f.mark_bar_ms!==e.first_bar_ms+(r.folds[i].test_end-1)*duration||
       !decimal(f.entry_open)||Number(f.entry_open)<=0||!decimal(f.terminal_close)||Number(f.terminal_close)<=0||
       !Array.isArray(f.scenarios)||f.scenarios.length!==3)throw Error('invalid diagnostic window');
    f.scenarios.forEach((v,j)=>{
      if(!v||v.multiplier!==multipliers[j]||!decimal(v.fee_pct)||!decimal(v.slippage_pct)||
         Number(v.fee_pct)>=100||Number(v.slippage_pct)>=100||!metric(v.strategy)||!metric(v.buy_hold)||
         !decimal(v.excess_return_pp,true))throw Error('invalid cost scenario');
      if(r.folds[i].selected===null){
        if(v.strategy.fills!==0||Number(v.strategy.return_pct)!==0||Number(v.strategy.fees)!==0||
           Number(v.strategy.position_value)!==0)throw Error('invalid skipped diagnostic');
      }else if(j===0&&Object.keys(r.folds[i].test).some(k=>v.strategy[k]!==r.folds[i].test[k]))throw Error('base mismatch');
    });
  });
  d.summary.forEach((v,j)=>{
    if(!v||v.multiplier!==multipliers[j]||v.total_folds!==r.folds.length||
       !['positive_folds','outperformed_benchmark_folds'].every(k=>count(v[k])&&v[k]<=v.total_folds)||
       !['mean_strategy_return_pct','mean_buy_hold_return_pct','mean_excess_return_pp','worst_strategy_return_pct'].every(k=>decimal(v[k],true)))throw Error('invalid diagnostic summary');
  });
}

export function validateResearch(r){
  if(!r||![1,2].includes(r.schema_version)||r.kind!=='uulab_walk_forward'||r.mode!=='paper'||r.live_enabled!==false||r.read_only!==true||!hash(r.run_id)||typeof r.generated_at!=='string'||!/(Z|[+-]\d\d:\d\d)$/.test(r.generated_at)||!Number.isFinite(Date.parse(r.generated_at)))throw Error('invalid research');
  const e=r.experiment,s=r.summary;
  if(!e||typeof e.symbol!=='string'||!/^([A-Z0-9._-]{1,24})\/(KRW|USDT)$/.test(e.symbol)||typeof e.timeframe!=='string'||!/^[1-9][0-9]{0,4}[mhdw]$/.test(e.timeframe)||!hash(e.fixture_sha256)||!count(e.bars)||e.bars>5000||!count(e.first_bar_ms)||e.first_bar_ms>253402300799999||!e.settings||!['initial_balance','fee_pct','slippage_pct'].every(k=>decimal(e.settings[k]))||!count(e.settings.warmup)||!count(e.settings.gap)||!count(e.settings.min_training_fills)||!Array.isArray(e.candidates)||e.candidates.length<1||e.candidates.length>8)throw Error('invalid experiment');
  const ids=e.candidates.map(c=>c.id);
  if(ids.some(v=>!id(v))||new Set(ids).size!==ids.length||e.candidates.some(c=>!['ma_cross','rsi'].includes(c.strategy)))throw Error('invalid candidates');
  if(!s||!['total_folds','evaluated_folds','skipped_folds','positive_folds','unused_tail_bars'].every(k=>count(s[k]))||s.total_folds<1||s.total_folds>24||s.evaluated_folds+s.skipped_folds!==s.total_folds||s.positive_folds>s.evaluated_folds||!['mean_test_return_pct','best_test_return_pct','worst_test_return_pct'].every(k=>decimal(s[k],true))||!Array.isArray(r.folds)||r.folds.length!==s.total_folds)throw Error('invalid summary');
  for(const f of r.folds){
    if(!['train_start','train_end','test_start','test_end'].every(k=>count(f[k]))||f.train_start>=f.train_end||f.train_end>f.test_start||f.test_start>=f.test_end||f.test_end>e.bars||!Array.isArray(f.train_scores)||f.train_scores.length!==ids.length)throw Error('invalid fold');
    if(f.train_scores.some((v,i)=>v.candidate_id!==ids[i]||!metric(v.metrics)))throw Error('invalid training');
    if(f.selected===null ? f.test!==null : (!ids.includes(f.selected)||!metric(f.test)))throw Error('invalid selection');
  }
  validateRobustness(r);
  return r;
}
export function createController({request,onData,onError}){let pending=null,last=null;return {refresh(){if(pending)return pending;pending=Promise.resolve().then(request).then(validateResearch).then(r=>{onData(r);last=r}).catch(e=>onError(e,last)).finally(()=>{pending=null});return pending}};}

function boot(){
  const $=s=>document.getElementById(s);if(!$('research-root'))return;
  let timer,busy=false;
  const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=String(value??'—');return node};
  const time=v=>new Intl.DateTimeFormat('ko-KR',{dateStyle:'short',timeStyle:'short',timeZone:'Asia/Seoul'}).format(new Date(v));
  function render(r){
    const e=r.experiment,s=r.summary,currency=e.symbol.split('/')[1];
    $('research-status').textContent='보고서 연결됨 · 과거 데이터 검증';$('research-notice').hidden=true;
    $('research-data').hidden=false;$('research-data').classList.remove('stale');
    $('mean').textContent=formatAmount(s.mean_test_return_pct)+'%';$('worst').textContent=formatAmount(s.worst_test_return_pct)+'%';
    $('evaluated').textContent=s.evaluated_folds+' / '+s.total_folds;$('skipped').textContent=s.skipped_folds+'개';
    $('context').textContent=`${e.symbol} · ${e.timeframe} · ${e.bars}개 봉 · 후보 ${e.candidates.length}개 · 간격 ${e.settings.gap}개 봉 · 준비 구간 최대 ${e.settings.warmup}개 봉`;
    $('assumptions').textContent=`각 구간 초기자금 ${formatAmount(e.settings.initial_balance)} ${currency} · 수수료 ${e.settings.fee_pct}% / 슬리피지 ${e.settings.slippage_pct}% · 학습 체결 최소 ${e.settings.min_training_fills}건`;
    $('source').textContent='로컬 입력 SHA-256 '+e.fixture_sha256+' · 출처 진본 인증 아님';
    $('generated').textContent='보고서 생성 '+time(r.generated_at)+' KST · 끝부분 미사용 '+s.unused_tail_bars+'개 봉';
    const duration=Number(e.timeframe.slice(0,-1))*{m:60000,h:3600000,d:86400000,w:604800000}[e.timeframe.at(-1)];
    const range=(a,b)=>time(e.first_bar_ms+a*duration)+' → '+time(e.first_bar_ms+(b-1)*duration);
    const cards=$('robustness-cards');cards.replaceChildren();
    $('robustness-note').textContent=r.schema_version===1
      ? '이 보고서에는 기준전략·비용 진단이 없습니다. --robustness로 새 파일을 생성하세요. 미실행은 0%가 아닙니다.'
      : `학습 선택 고정 · 최초 배분 ${e.settings.position_size_pct}%로 매수 후 보유 · 검증 두 번째 봉 시가 진입 / 마지막 종가 평가. 현금 기준 0%, 이자 없음. 기준전략에는 손절·낙폭 차단이 없어 위험 수준이 같다는 뜻은 아닙니다. 강제 청산 비용은 제외합니다.`;
    const rate=v=>Number(v).toLocaleString('ko-KR',{maximumFractionDigits:4}); // Display only; all accounting is server Decimal.
    if(r.robustness)for(const v of r.robustness.summary){
      const card=document.createElement('article');
      card.append(text('h3',`거래비용 ${v.multiplier}×`));
      const add=(label,value,unit)=>{const line=document.createElement('p');line.append(text('span',label));const n=text('strong',rate(value)+unit);n.title=value+unit;line.append(n);card.append(line)};
      add('전략 평균',v.mean_strategy_return_pct,'%');add('매수보유 평균',v.mean_buy_hold_return_pct,'%');add('기준전략 대비',v.mean_excess_return_pp,'%p');
      card.append(text('small',`기준전략 초과 ${v.outperformed_benchmark_folds}/${v.total_folds}구간 · 현금 초과 ${v.positive_folds}/${v.total_folds}구간`));
      cards.append(card);
    }
    const body=$('folds');body.replaceChildren();
    r.folds.forEach((f,index)=>{
      const row=document.createElement('tr');const window=document.createElement('td');window.append(text('strong','구간 '+(index+1)),text('small','학습 '+range(f.train_start,f.train_end)),text('small','검증 '+range(f.test_start,f.test_end)));
      const selection=document.createElement('td');selection.append(text('strong',f.selected??'선택 없음 · 현금 대기'));const sl=text('small','학습 선택');sl.className='mobile-label';selection.prepend(sl);
      const details=document.createElement('details');details.append(text('summary','학습 비교 근거'));
      for(const score of f.train_scores)details.append(text('p',`${score.candidate_id}: ${formatAmount(score.metrics.return_pct)}% · ${score.metrics.fills}건${score.metrics.fills<e.settings.min_training_fills?' · 표본 부족':''}`));
      selection.append(details);
      const result=document.createElement('td');const rl=text('small','이후 구간 검증');rl.className='mobile-label';result.append(rl);result.append(text('strong',f.test?formatAmount(f.test.return_pct)+'%':'건너뜀 · 현금 0%'));
      if(f.test)result.append(text('small',`체결 ${f.test.fills}건 · 수수료 ${formatAmount(f.test.fees)} ${currency}`),text('small',`미청산 평가액 ${formatAmount(f.test.position_value)} ${currency}`),text('small',`손실/낙폭 차단 ${f.test.daily_loss_halted||f.test.drawdown_halted?'발동':'미발동'}`));
      if(r.robustness){
        const diagnostics=document.createElement('details');diagnostics.className='cost-details';
        diagnostics.append(text('summary','기준전략 · 비용별 결과'));
        for(const v of r.robustness.folds[index].scenarios){
          const line=text('p',`${v.multiplier}×: 전략 ${rate(v.strategy.return_pct)}% / 매수보유 ${rate(v.buy_hold.return_pct)}% / 차이 ${rate(v.excess_return_pp)}%p`);
          line.title=`수수료 ${v.fee_pct}% · 슬리피지 ${v.slippage_pct}% · 차이 원값 ${v.excess_return_pp}%p`;
          diagnostics.append(line);
          diagnostics.append(text('small',`전략 체결 ${v.strategy.fills}건 · 수수료 ${formatAmount(v.strategy.fees)} ${currency} · 위험 차단 ${v.strategy.daily_loss_halted||v.strategy.drawdown_halted?'발동':'미발동'}`));
        }
        result.append(diagnostics);
      }
      row.append(window,selection,result);body.append(row);
    });
    $('last-fetch').textContent='조회 '+time(Date.now())+' KST · 실시간 운용 아님';
  }
  function error(_e,last){$('research-status').textContent=last?'갱신 실패 · 이전 보고서':'보고서 미연결';$('research-notice').hidden=false;$('research-notice').textContent=last?'마지막 정상 보고서를 유지합니다. 아래 결과는 새로 확인된 정보가 아닙니다.':'검증된 보고서를 읽지 못했습니다. --research-report 경로를 확인하세요. 수익률 0 또는 전략 부재를 뜻하지 않습니다.';$('research-data').classList.add('stale');}
  async function request(){const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),5000);try{const response=await fetch('/api/research',{cache:'no-store',credentials:'omit',signal:abort.signal});if(!response.ok)throw Error('unavailable');return await response.json()}finally{clearTimeout(timeout)}}
  const controller=createController({request,onData:render,onError:error});
  async function refresh(){if(busy)return;busy=true;clearTimeout(timer);$('research-refresh').disabled=true;try{await controller.refresh()}finally{busy=false;$('research-refresh').disabled=false;timer=setTimeout(()=>{if(!document.hidden)refresh()},15000)}}
  $('research-refresh').addEventListener('click',refresh);document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh()});refresh();
}
if(typeof document!=='undefined')boot();
