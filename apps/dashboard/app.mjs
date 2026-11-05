// Local read-only operations UI. No credentials, order controls or invented P&L.
const LABELS = Object.freeze({reserved:'자금 예약',submitting:'전송 확인 중',unknown:'결과 불명',
  accepted:'접수 확인',partially_filled:'부분 체결',cancel_pending:'취소 확인 중',
  cancelled:'취소 확정',rejected:'거절 확정',filled:'체결 완료'});
const SOURCES = Object.freeze({paper_journal:'로컬 모의 주문 원장',synthetic_demo:'합성 데이터 · 기능 검증용 데모',
  legacy_paper_journal:'이전 형식의 모의 주문 원장'});
const isCount = value => Number.isSafeInteger(value) && value >= 0;
const isText = value => typeof value === 'string' && value.length > 0 && value.length <= 128;
const isAmount = value => typeof value === 'string' && value.length <= 100 && /^(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value));
const isCurrency = value => typeof value === 'string' && /^[A-Z]{2,16}$/.test(value);

export function validateSnapshot(value) {
  if (!value || value.mode !== 'paper' || value.live_enabled !== false || value.read_only !== true ||
      value.journal_connected !== true || value.accounting_available !== false || !Object.hasOwn(SOURCES,value.source) ||
      !toUTCDate(value.as_of) || !Array.isArray(value.orders) || !Array.isArray(value.events)) throw new Error('invalid snapshot');
  const s=value.summary, p=value.pagination;
  if (!s || ![s.total_orders,s.active_orders,s.needs_reconciliation].every(isCount) ||
      s.active_orders>s.total_orders || s.needs_reconciliation>s.active_orders ||
      !s.reserved_by_currency || typeof s.reserved_by_currency!=='object' || Array.isArray(s.reserved_by_currency) ||
      !p || ![p.limit,p.offset,p.total].every(isCount) || p.limit<1 || p.limit>100 ||
      value.orders.length>p.limit || typeof p.has_more!=='boolean' || typeof value.audit_available!=='boolean') throw new Error('invalid summary');
  for (const [currency,amount] of Object.entries(s.reserved_by_currency))
    if (!isCurrency(currency) || !isAmount(amount)) throw new Error('invalid currency amount');
  for (const order of value.orders)
    if (!isText(order.client_id) || !Object.hasOwn(LABELS,order.status) || !isAmount(order.reserved) ||
        !isCurrency(order.currency) || typeof order.updated_at!=='string' ||
        (order.symbol!==null && order.symbol!==undefined && !isText(order.symbol))) throw new Error('invalid order');
  for (const event of value.events)
    if (!isCount(event.id) || !isText(event.client_id) || !Object.hasOwn(LABELS,event.to_status) ||
        (event.from_status!==null && !Object.hasOwn(LABELS,event.from_status)) ||
        !isAmount(event.reserved) || typeof event.occurred_at!=='string') throw new Error('invalid event');
  return value;
}

export function toUTCDate(value) {
  if (typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}/.test(value)) return null;
  let normalized=value.replace(' ','T');
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) normalized+='T00:00:00Z';
  else if (!/(Z|[+-]\d\d:\d\d)$/.test(normalized)) normalized+='Z';
  const date=new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}
export function formatAmount(value) {
  // Keep fractional precision; never use parseFloat for financial display.
  if (!isAmount(value)) return '—';
  if (/[eE]/.test(value)) return value;
  const [integer,fraction]=value.split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction ? '.'+fraction : '');
}
export function createRefreshController({request,onData,onError}) {
  let pending=null, lastGood=null;
  return {refresh() {
    if (pending) return pending;
    pending=Promise.resolve().then(request).then(validateSnapshot).then(data=>{
      onData(data); lastGood=data;
    }).catch(error=>onError(error,lastGood)).finally(()=>{pending=null;});
    return pending;
  }};
}

function boot() {
  const $=id=>document.getElementById(id);
  let offset=0, lastSuccess=null, lastSnapshot=null, timer=null, failed=false, busy=false;
  const limit=25;
  const time=value=>{
    const date=toUTCDate(value);
    return date ? new Intl.DateTimeFormat('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZone:'Asia/Seoul'}).format(date) : '—';
  };
  const text=(tag,value,className)=>{
    const el=document.createElement(tag); el.textContent=String(value ?? '—');
    if(className) el.className=className;
    return el;
  };
  const amountText=(amount,currency)=>formatAmount(amount)+' '+(currency==='UNSPECIFIED'?'통화 미지정':currency);
  function setBusy(value) {
    busy=value;
    $('refresh').disabled=value;
    $('status-filter').disabled=value;
    $('previous').disabled=value || offset===0;
    $('next').disabled=value || !lastSnapshot?.pagination.has_more;
    $('orders-panel').setAttribute('aria-busy',String(value));
  }
  function render(data) {
    lastSnapshot=data; lastSuccess=Date.now(); failed=false;
    $('connection').textContent='원장 연결됨'; $('connection').dataset.state='connected';
    $('source').textContent=SOURCES[data.source];
    $('source').dataset.demo=String(data.source==='synthetic_demo');
    $('connection-notice').hidden=true;
    $('data-region').classList.remove('stale');
    $('total-orders').textContent=data.summary.total_orders.toLocaleString('ko-KR');
    $('active-orders').textContent=data.summary.active_orders.toLocaleString('ko-KR');
    $('reconcile-count').textContent=data.summary.needs_reconciliation.toLocaleString('ko-KR');
    $('last-order-update').textContent=time(data.summary.last_order_update);
    $('reservation-values').replaceChildren();
    for(const [currency,amount] of Object.entries(data.summary.reserved_by_currency))
      $('reservation-values').append(text('div',amountText(amount,currency),'amount-line'));
    if(!Object.keys(data.summary.reserved_by_currency).length)
      $('reservation-values').append(text('div','예약 없음','amount-line'));
    const uncertain=data.summary.needs_reconciliation;
    $('reconcile-message').textContent=uncertain
      ? `${uncertain}건은 전송 또는 취소 결과 확인이 필요합니다. 주문 실패로 단정해 재전송하지 마세요. 이 화면은 증권사 자동 대사를 수행하지 않습니다.`
      : '원장에 기록된 불명확한 전송·취소 상태가 없습니다. 실제 증권사 상태를 검증했다는 의미는 아닙니다.';
    $('recovery').dataset.warning=String(uncertain>0);
    const body=$('order-rows'); body.replaceChildren();
    for(const order of data.orders) {
      const row=document.createElement('tr');
      const id=document.createElement('td'); id.append(text('code',order.client_id));
      id.append(text('small',order.symbol ?? '종목 정보 없음','secondary'));
      const state=document.createElement('td'); const tag=text('span',LABELS[order.status],'state');
      tag.dataset.state=order.status; state.append(tag);
      row.append(id,state,text('td',amountText(order.reserved,order.currency),'numeric'),text('td',time(order.updated_at),'timestamp'));
      body.append(row);
    }
    $('orders-empty').hidden=data.orders.length>0;
    $('orders-empty').textContent=data.summary.total_orders===0?'원장이 연결되었습니다. 아직 저장된 주문이 없습니다.':'선택한 상태에 해당하는 주문이 없습니다.';
    const first=data.orders.length ? offset+1 : 0;
    $('page-status').textContent=`${first}–${offset+data.orders.length} / ${data.pagination.total}건`;
    $('audit-rows').replaceChildren();
    for(const event of data.events) {
      const row=document.createElement('tr');
      row.append(text('td','#'+event.id,'secondary'),text('td',event.client_id),
        text('td',(event.from_status ? LABELS[event.from_status] : (event.kind==='migration'?'이전 원장 이관':'기록 시작'))+' → '+LABELS[event.to_status]),
        text('td',time(event.occurred_at),'timestamp'));
      $('audit-rows').append(row);
    }
    $('audit-empty').hidden=data.events.length>0;
    $('audit-empty').textContent=data.audit_available?'아직 기록된 상태 변경이 없습니다.':'이전 원장에는 감사 기록이 없습니다. 기록을 임의로 생성하지 않습니다.';
    $('journal-version').textContent=`v${data.schema_version ?? '이전'}`;
    updateAge();
  }
  function showError(_error,previous) {
    failed=true;
    $('connection').textContent=previous?'갱신 중단':'미연결';
    $('connection').dataset.state='disconnected';
    $('connection-notice').hidden=false;
    $('connection-notice').textContent=previous
      ? '새 데이터를 가져오지 못했습니다. 아래는 마지막 성공 시점의 정보이며 현재 상태가 아닐 수 있습니다.'
      : '원장에 연결할 수 없습니다. 로컬 서버와 --journal 경로를 확인하세요. 빈 화면은 주문이 없다는 뜻이 아닙니다.';
    $('data-region').classList.add('stale'); updateAge();
  }
  async function request() {
    const params=new URLSearchParams({limit:String(limit),offset:String(offset)});
    if($('status-filter').value) params.set('status',$('status-filter').value);
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),5000);
    try {
      const response=await fetch('/api/snapshot?'+params,{cache:'no-store',credentials:'omit',signal:controller.signal});
      if(!response.ok) throw new Error('snapshot unavailable');
      return await response.json();
    } finally {clearTimeout(timeout);}
  }
  const controller=createRefreshController({request,onData:render,onError:showError});
  async function refresh() {
    if(busy) return;
    clearTimeout(timer); setBusy(true);
    try {await controller.refresh();} finally {
      setBusy(false); timer=setTimeout(()=>{if(!document.hidden) refresh();},15000);
    }
  }
  function updateAge() {
    $('freshness').textContent=lastSuccess
      ? `마지막 성공 조회 ${Math.max(0,Math.floor((Date.now()-lastSuccess)/1000))}초 전${failed?' · 과거 데이터':''}`
      : '성공한 조회 없음';
  }
  $('refresh').addEventListener('click',refresh);
  $('status-filter').addEventListener('change',()=>{offset=0; refresh();});
  $('previous').addEventListener('click',()=>{offset=Math.max(0,offset-limit); refresh();});
  $('next').addEventListener('click',()=>{offset+=limit; refresh();});
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer); if(!document.hidden) refresh();});
  setInterval(updateAge,1000);
  refresh();
}
if(typeof document!=='undefined') boot();
