/* Saved account observations, not heartbeat, quote freshness, or order approval. */
const clone = value => JSON.parse(JSON.stringify(value));
const insist = value => { if (!value) throw new Error('invalid_paper_account_report'); };
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 128 && !/[\x00-\x1f\x7f]/.test(value);
const count = value => Number.isSafeInteger(value) && value >= 0 && value <= 10000;
function decimal(value) {
  insist(typeof value === 'string' && value.length <= 128);
  const match = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,3}))?$/.exec(value);
  insist(match);
  const [, sign, whole, fraction = '', power = '0'] = match;
  const exponent = Number(power);
  insist(Math.abs(exponent) <= 120 && (sign !== '-' || !/[1-9]/.test(whole + fraction)));
  const digits = whole + fraction, point = whole.length + exponent;
  const full = point <= 0 ? '0.' + '0'.repeat(-point) + digits
    : point >= digits.length ? digits + '0'.repeat(point - digits.length)
    : digits.slice(0, point) + '.' + digits.slice(point);
  const [integer, frac = ''] = full.split('.');
  return [(integer.replace(/^0+(?=\d)/, '') || '0'), frac.replace(/0+$/, '')];
}
function utcStamp(value) {
  insist(typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00$/.test(value));
  const date = new Date(value);
  insist(Number.isFinite(date.getTime()) && date.toISOString().slice(0, 19) === value.slice(0, 19));
}
export function validateReport(r) {
  insist(r && r.schema_version === 1 && r.source === 'durable_paper_session' && r.mode === 'paper');
  insist(r.read_only === true && r.live_enabled === false);
  insist(/^[a-f0-9]{32}$/.test(r.account_id) && /^[a-f0-9]{64}$/.test(r.digest));
  insist(typeof r.revision === 'string' && /^(0|[1-9]\d{0,18})$/.test(r.revision) && BigInt(r.revision) < 2n ** 62n);
  insist([1, 2, 3].includes(r.session_version)); utcStamp(r.observed_at);
  const c = r.control, p = r.policy, o = r.observation, a = r.account, orders = r.orders;
  insist(c && typeof c.enabled === 'boolean');
  if (c.enabled) {
    insist(['active', 'reduce_only', 'halted'].includes(c.mode) && Number.isSafeInteger(c.generation) && c.generation >= 0 && c.generation <= 2147483647);
    insist(['created', 'operator', 'risk_review', 'maintenance'].includes(c.reason) && r.session_version === 3);
    if (c.generation === 0) insist(c.mode === 'halted' && c.reason === 'created');
    else insist(c.reason !== 'created');
    if (c.generation === 2147483647) insist(c.mode === 'halted');
  } else insist(c.mode === null && c.generation === null && c.reason === null && r.session_version !== 3);
  insist(p && typeof p.enabled === 'boolean');
  insist(p.max_quote_age_ms === null || (Number.isSafeInteger(p.max_quote_age_ms) && p.max_quote_age_ms > 0 && p.max_quote_age_ms <= 86400000));
  if (p.max_order_notional !== null) insist(/[1-9]/.test(decimal(p.max_order_notional).join('')));
  if (!p.enabled) insist(p.max_quote_age_ms === null && p.max_order_notional === null);
  else insist(p.max_quote_age_ms !== null || p.max_order_notional !== null);
  if (r.session_version === 1) insist(!p.enabled);
  if (r.session_version === 2) insist(p.enabled);
  insist(o && o.process_state === 'unobserved' && o.order_authorization === 'not_assessed' && o.quote_receipts === 'unavailable_to_observer' && o.valuation_basis === 'saved_marks');
  insist(count(o.missing_marks_count) && Array.isArray(o.missing_marks) && o.missing_marks.length === Math.min(o.missing_marks_count, 100) && o.missing_marks.every(text));
  insist(a && ['USDT', 'KRW'].includes(a.quote_currency));
  for (const key of ['initial_cash', 'cash', 'total_fees']) decimal(a[key]);
  for (const key of ['marked_equity', 'position_value']) {
    if (a[key] === null) insist(o.missing_marks_count > 0); else decimal(a[key]);
  }
  insist(count(a.holdings_count) && Array.isArray(a.holdings) && a.holdings.length === Math.min(a.holdings_count, 100) && a.holdings_truncated === (a.holdings_count > 100));
  for (const h of a.holdings) {
    insist(text(h.asset)); decimal(h.quantity);
    if (h.mark === null) insist(h.marked_value === null && h.mark_timestamp_ms === null);
    else { decimal(h.mark); decimal(h.marked_value); insist(Number.isSafeInteger(h.mark_timestamp_ms) && h.mark_timestamp_ms >= 0 && Number.isFinite(new Date(h.mark_timestamp_ms).getTime())); }
  }
  insist(orders && Array.isArray(orders.items));
  for (const key of ['total', 'unresolved', 'confirmed_filled', 'account_fills', 'unconfirmed_account_fills']) insist(count(orders[key]));
  insist(orders.total === orders.unresolved + orders.confirmed_filled && orders.unconfirmed_account_fills <= orders.unresolved);
  insist(orders.account_fills === orders.confirmed_filled + orders.unconfirmed_account_fills);
  insist(orders.items.length === Math.min(orders.total, 100) && orders.truncated === (orders.total > 100));
  const ids = new Set();
  for (const item of orders.items) {
    insist(text(item.client_order_id) && !ids.has(item.client_order_id)); ids.add(item.client_order_id);
    insist(text(item.symbol) && ['buy', 'sell'].includes(item.side) && ['filled', 'unknown', 'submitting'].includes(item.status));
    insist(typeof item.account_fill_present === 'boolean');
    for (const key of ['amount', 'filled', 'remaining']) decimal(item[key]);
  }
  return r;
}
export function formatDecimal(value) {
  if (value === null) return '미산출';
  const [whole, fraction] = decimal(value);
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction ? '.' + fraction.slice(0, 8) + (fraction.length > 8 ? '…' : '') : '');
}
export function controlLabel(control) {
  if (!control.enabled) return '제어 미설정';
  return ({active: '정상 모드 · 저장값', reduce_only: '축소 전용 · 저장값', halted: '중단 · 저장값'})[control.mode];
}
export function ageLabel(timestamp, now = Date.now()) {
  if (timestamp === null) return '가격 시각 없음';
  if (!Number.isSafeInteger(timestamp) || !Number.isFinite(now)) return '시각 확인 필요';
  if (timestamp > now) return '미래 시각 · 확인 필요';
  const seconds = Math.floor((now - timestamp) / 1000);
  return seconds < 60 ? `${seconds}초 전` : seconds < 3600 ? `${Math.floor(seconds / 60)}분 전`
    : seconds < 86400 ? `${Math.floor(seconds / 3600)}시간 전` : `${Math.floor(seconds / 86400)}일 전`;
}
export function createMonitorState() {
  let report = null, stale = false;
  return {
    accept(value) {
      try {
        validateReport(value);
        if (report) insist(value.account_id === report.account_id && BigInt(value.revision) >= BigInt(report.revision)
          && (value.revision !== report.revision || value.digest === report.digest));
        report = clone(value); stale = false;
      } catch (error) { stale = true; throw error; }
    },
    fail() { stale = true; },
    view() { return {report: report === null ? null : clone(report), stale}; }
  };
}
function start() {
  const state = createMonitorState(); let busy = false;
  const byId = id => document.getElementById(id);
  const set = (id, value) => { byId(id).textContent = value; };
  const cell = (value, label, exact = null) => {
    const td = document.createElement('td'); td.textContent = value; td.dataset.label = label;
    if (exact !== null) td.title = exact; return td;
  };
  function draw() {
    const {report: r, stale} = state.view();
    document.body.classList.toggle('stale', stale);
    byId('notice').hidden = !stale;
    set('request-status', busy ? '파일 확인 중' : stale ? '현재 미확인' : r ? '읽기 완료' : '아직 조회하지 않음');
    if (!r) return;
    set('account-id', r.account_id.slice(0, 8)); set('revision', r.revision);
    set('observed-at', new Date(r.observed_at).toLocaleString('ko-KR'));
    set('operating-mode', (stale ? '이전 검사: ' : '') + controlLabel(r.control));
    set('mode-reason', r.control.enabled ? `변경 번호 ${r.control.generation} · ${r.control.reason}` : '기존 계좌 형식 · 제어 기능 미설정');
    for (const [id, key] of [['cash','cash'], ['equity','marked_equity'], ['fees','total_fees']]) {
      set(id, formatDecimal(r.account[key])); byId(id).title = r.account[key] ?? '가격 자료 부족';
    }
    document.querySelectorAll('[data-currency]').forEach(el => { el.textContent = r.account.quote_currency; });
    set('unresolved', String(r.orders.unresolved));
    set('pending-detail', `계좌 반영 · 응답 미확정 ${r.orders.unconfirmed_account_fills}건`);
    set('order-count', `전체 ${r.orders.total}건 · 계좌 체결 ${r.orders.account_fills}건`);
    set('order-limit', r.orders.truncated ? '상세는 미해결 주문 우선 100건입니다. 집계에는 전체 기록을 포함합니다.' : '체결이 계좌에 반영되어도 주문 응답 확인은 끝나지 않았을 수 있습니다.');
    set('notional-policy', r.policy.max_order_notional === null ? '미설정' : `${formatDecimal(r.policy.max_order_notional)} ${r.account.quote_currency}`);
    set('quote-policy', r.policy.max_quote_age_ms === null ? '미설정' : `${r.policy.max_quote_age_ms} ms`);
    byId('holdings').replaceChildren();
    for (const h of r.account.holdings) {
      const tr = document.createElement('tr');
      tr.append(cell(h.asset,'자산'), cell(formatDecimal(h.quantity),'수량',h.quantity), cell(formatDecimal(h.mark),'저장 가격',h.mark), cell(formatDecimal(h.marked_value),'평가액',h.marked_value));
      const stamp=cell(ageLabel(h.mark_timestamp_ms),'가격 시각');
      if(h.mark_timestamp_ms !== null){stamp.dataset.quoteMs=String(h.mark_timestamp_ms);stamp.title=new Date(h.mark_timestamp_ms).toISOString();}
      tr.append(stamp);byId('holdings').append(tr);
    }
    byId('no-holdings').hidden=r.account.holdings_count !== 0;
    set('holdings-limit',r.account.holdings_truncated ? `총 ${r.account.holdings_count}종 중 100종 표시` : '평가액은 저장 가격 기준입니다. 현재 시세와 다를 수 있습니다.');
    byId('orders').replaceChildren();
    for(const item of r.orders.items){
      const tr=document.createElement('tr');
      const label=({filled:'확인된 체결',unknown:'결과 미확정',submitting:'전송 의도 저장'})[item.status];
      tr.append(cell(item.client_order_id,'주문 ID'),cell(item.symbol,'종목'),cell(item.side==='buy'?'매수':'매도','방향'),cell(formatDecimal(item.amount),'주문수량',item.amount),cell(label,'저장 상태'),cell(item.account_fill_present?'반영됨':'미확인','계좌 체결'));
      byId('orders').append(tr);
    }
    byId('no-orders').hidden=r.orders.total !== 0;
    set('digest',r.digest);
  }
  async function refresh(){
    if(busy)return;busy=true;byId('refresh').disabled=true;draw();
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);
    try{
      const response=await fetch('/api/paper-session',{cache:'no-store',signal:abort.signal});
      if(!response.ok || !response.headers.get('content-type')?.includes('application/json'))throw new Error('unavailable');
      const body=await response.text();if(body.length>1000000)throw new Error('too_large');
      state.accept(JSON.parse(body));
    }catch{state.fail();}
    finally{clearTimeout(timer);busy=false;byId('refresh').disabled=false;draw();}
  }
  byId('refresh').addEventListener('click',refresh);
  setInterval(()=>{if(byId('auto-refresh').checked && document.visibilityState==='visible')refresh();},10000);
  setInterval(()=>document.querySelectorAll('[data-quote-ms]').forEach(el=>{el.textContent=ageLabel(Number(el.dataset.quoteMs));}),1000);
  refresh();
}
if(typeof document !== 'undefined') start();
