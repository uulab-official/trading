import {validatePage, FILTERS, count} from './paper-order-contract.mjs';
import {mountOrderDetail} from './paper-order-detail.mjs';
export {validatePage} from './paper-order-contract.mjs';
const clone = value => JSON.parse(JSON.stringify(value));
const requireValue = value => { if (!value) throw new Error('invalid_paper_order_page'); };

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
    requireRestart() { active = null; stale = true; restartRequired = true; },
    view() { return {report: report === null ? null : clone(report),busy:active !== null,stale,restartRequired}; }
  };
}

function mount() {
  const state = createOrderBrowser(), byId = id => document.getElementById(id);
  const detail = mountOrderDetail(document, () => { state.requireRestart(); draw(); });
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
      const actions=document.createElement('td'); actions.dataset.label='주문 상세';
      const button=document.createElement('button'); button.type='button'; button.textContent='상세 보기';
      button.disabled=v.busy || v.stale; button.setAttribute('aria-label',i.client_order_id+' 주문 상세 보기');
      button.addEventListener('click',()=>detail.select(p,i.client_order_id,button)); actions.append(button); row.append(actions);
      return row;
    });
    byId('orders').replaceChildren(...rows);
  }
  async function read(options) {
    let req;
    try {req=state.begin(options);} catch {return;}
    if (!req) return;
    detail.clear();
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
