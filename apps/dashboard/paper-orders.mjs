/* Read-only order pages. Stored status is not current venue evidence. */
const clone = value => JSON.parse(JSON.stringify(value));
const requireValue = value => { if (!value) throw new Error('invalid_paper_order_page'); };
const FILTERS = ['all', 'unresolved', 'submitting', 'unknown', 'filled'];
const count = x => Number.isSafeInteger(x) && x >= 0 && x <= 10000;
const identifier = x => typeof x === 'string' && x.length > 0 && x.length <= 128 && x === x.trim() && !/[\x00-\x1f\x7f]/.test(x);
const revision = x => typeof x === 'string' && /^(0|[1-9]\d{0,18})$/.test(x) && BigInt(x) < 2n ** 62n;
function units(value) {
  requireValue(typeof value === 'string' && value.length <= 100);
  const m = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,2}))?$/.exec(value);
  requireValue(m);
  const digits = m[2] + (m[3] || ''), power = Number(m[4] || '0') - (m[3] || '').length;
  requireValue(digits.length <= 60 && power >= -30 && power <= 30 && (m[1] !== '-' || !/[1-9]/.test(digits)));
  const result = BigInt(digits) * (10n ** BigInt(power + 30));
  requireValue(result <= 10n ** 60n);
  return result;
}
function stamp(value) {
  requireValue(typeof value === 'string');
  const match = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d{1,6})?[+-](\d\d):(\d\d)$/.exec(value);
  requireValue(match);
  const [year, month, day, hour, minute, second, zoneHour, zoneMinute] = match.slice(1).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31,leap ? 29 : 28,31,30,31,30,31,31,30,31,30,31];
  requireValue(year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month-1]);
  requireValue(hour < 24 && minute < 60 && second < 60 && zoneHour < 24 && zoneMinute < 60);
  requireValue(Number.isFinite(Date.parse(value)));
}
export function validatePage(p) {
  requireValue(p && p.schema_version === 1 && p.source === 'durable_paper_order_page' && p.mode === 'paper');
  requireValue(p.read_only === true && p.live_enabled === false && [1,2,3].includes(p.session_version));
  requireValue(typeof p.account_id === 'string' && /^[a-f0-9]{32}$/.test(p.account_id));
  requireValue(typeof p.digest === 'string' && /^[a-f0-9]{64}$/.test(p.digest) && revision(p.revision));
  stamp(p.observed_at);
  const o = p.orders;
  requireValue(o && count(o.total) && count(o.matched) && o.matched <= o.total && FILTERS.includes(o.status));
  requireValue(count(o.offset) && Number.isSafeInteger(o.limit) && o.limit > 0 && o.limit <= 100);
  requireValue(o.offset === 0 || o.offset < o.matched);
  requireValue(o.has_previous === (o.offset > 0) && o.has_next === (o.offset + o.limit < o.matched));
  requireValue(Array.isArray(o.items) && o.items.length === Math.min(o.limit, Math.max(0, o.matched - o.offset)));
  const ids = new Set();
  for (const i of o.items) {
    requireValue(i && identifier(i.client_order_id) && !ids.has(i.client_order_id)); ids.add(i.client_order_id);
    requireValue(identifier(i.symbol) && ['buy','sell'].includes(i.side) && i.order_type === 'market');
    requireValue(['submitting','unknown','filled'].includes(i.status) && typeof i.account_fill_present === 'boolean');
    requireValue(typeof i.cancel_requested === 'boolean');
    requireValue(i.exchange_order_id === null || identifier(i.exchange_order_id));
    requireValue(i.strategy_id === null || identifier(i.strategy_id));
    if (i.price !== null) requireValue(units(i.price) > 0n);
    const amount = units(i.amount), filled = units(i.filled), remaining = units(i.remaining);
    requireValue(amount > 0n && amount === filled + remaining);
    if (i.status === 'filled') requireValue(remaining === 0n && i.account_fill_present && identifier(i.exchange_order_id));
    else requireValue(filled === 0n);
    requireValue(o.status === 'all' || (o.status === 'unresolved' && i.status !== 'filled') || i.status === o.status);
    stamp(i.created_at); stamp(i.updated_at); requireValue(Date.parse(i.updated_at) >= Date.parse(i.created_at));
  }
  return p;
}

export function createOrderBrowser() {
  let report = null, active = null, sequence = 0, stale = false, restartRequired = false;
  return {
    begin({reset=false, status=report?.orders.status || 'all', offset=0, limit=50}={}) {
      requireValue(typeof reset === 'boolean' && FILTERS.includes(status) && count(offset));
      requireValue(Number.isSafeInteger(limit) && limit > 0 && limit <= 100 && (!reset || offset === 0));
      if (active || (restartRequired && !reset)) return null;
      if (!reset) requireValue(report && status === report.orders.status && limit === report.orders.limit);
      const q = new URLSearchParams({offset:String(offset),limit:String(limit),status});
      if (!reset) { q.set('revision',report.revision); q.set('digest',report.digest); }
      active = {id:++sequence, reset, status, offset, limit};
      return {...active,url:'/api/paper-session/orders?' + q.toString()};
    },
    accept(id, value) {
      if (!active || active.id !== id) return false;
      try {
        validatePage(value);
        requireValue(value.orders.offset === active.offset && value.orders.limit === active.limit && value.orders.status === active.status);
        if (report) {
          requireValue(value.account_id === report.account_id && value.session_version === report.session_version);
          requireValue(BigInt(value.revision) >= BigInt(report.revision));
          if (!active.reset) requireValue(value.revision === report.revision && value.digest === report.digest);
          if (value.revision === report.revision) requireValue(value.digest === report.digest);
        }
        report = clone(value); active = null; stale = false; restartRequired = false; return true;
      } catch (error) { active = null; stale = true; throw error; }
    },
    fail(id, reason='unavailable') {
      if (!active || active.id !== id) return false;
      active = null; stale = true; if (reason === 'changed') restartRequired = true; return true;
    },
    view() { return {report: report === null ? null : clone(report),busy:active !== null,stale,restartRequired}; }
  };
}

function mount() {
  const state = createOrderBrowser(), byId = id => document.getElementById(id);
  const labels = {all:'전체',unresolved:'미해결',submitting:'전송 의도 저장',unknown:'결과 미확인',filled:'체결 확인'};
  const set = (id, text) => { byId(id).textContent = text; };
  function draw() {
    const v=state.view(), p=v.report;
    byId('refresh').disabled=v.busy; byId('filter').disabled=v.busy;
    byId('previous').disabled=v.busy || v.restartRequired || !p?.orders.has_previous;
    byId('next').disabled=v.busy || v.restartRequired || !p?.orders.has_next;
    byId('notice').hidden=!v.stale;
    set('notice',v.restartRequired ? '계좌 저장 내용이 변경되었습니다. 서로 다른 시점의 주문을 섞지 않기 위해 이동을 멈췄습니다. ‘최신 기록으로 처음부터’를 눌러 다시 조회하세요.' : '현재 조회를 확인하지 못했습니다. 남아 있는 목록은 이전 조회 결과입니다. 최신 상태나 주문 가능 여부를 의미하지 않습니다.');
    set('request-status',v.busy?'조회 중…':v.stale?'이전 조회 · 현재 미확인':p?'저장 기록 조회 완료':'조회 대기');
    document.body.classList.toggle('stale',v.stale);
    if (!p) return;
    set('total',String(p.orders.total)); set('matched',String(p.orders.matched));
    set('page-range',p.orders.matched ? `${p.orders.offset+1}–${p.orders.offset+p.orders.items.length} / ${p.orders.matched}건` : '해당 상태의 주문 없음');
    set('filter-label',labels[p.orders.status]);
    set('revision',p.revision); set('account-id',p.account_id); set('observed-at',p.observed_at); set('digest',p.digest);
    byId('empty').hidden=p.orders.items.length!==0;
    const rows=p.orders.items.map(i=>{
      const row=document.createElement('tr');
      const values=[i.client_order_id,`${i.symbol} · ${i.side==='buy'?'매수':'매도'}`,i.amount,`${i.filled} / ${i.remaining}`,labels[i.status],i.account_fill_present?'기록 있음':'기록 없음'];
      const headings=['고객 주문 ID','종목 · 방향','주문수량','확인 체결 / 잔량','저장 상태','계좌 체결'];
      values.forEach((value,index)=>{const cell=document.createElement('td');cell.dataset.label=headings[index];cell.textContent=value;cell.title=value;row.append(cell);});
      return row;
    });
    byId('orders').replaceChildren(...rows);
  }
  async function read(options) {
    let req;
    try {req=state.begin(options);} catch {return;}
    if (!req) return;
    draw();
    const controller=new AbortController();
    const timeout=setTimeout(()=>{state.fail(req.id);controller.abort();draw();},10000);
    try {
      const response=await fetch(req.url,{cache:'no-store',credentials:'same-origin',signal:controller.signal});
      if (response.status===409) {state.fail(req.id,'changed');return;}
      if (!response.ok) throw new Error('order_page_unavailable');
      state.accept(req.id,await response.json());
    } catch {state.fail(req.id);}
    finally {clearTimeout(timeout);draw();}
  }
  const reset=()=>read({reset:true,status:byId('filter').value});
  byId('refresh').addEventListener('click',reset);
  byId('filter').addEventListener('change',reset);
  byId('previous').addEventListener('click',()=>{const p=state.view().report;if(p) read({offset:Math.max(0,p.orders.offset-p.orders.limit)});});
  byId('next').addEventListener('click',()=>{const p=state.view().report;if(p) read({offset:p.orders.offset+p.orders.limit});});
  reset();
}
if (typeof document !== 'undefined' && document.getElementById('paper-orders-browser')) mount();
