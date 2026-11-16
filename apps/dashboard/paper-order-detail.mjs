/* One pinned saved order + its simulated account fill. Never an execution API. */
import {validatePage, units, identifier, stamp} from './paper-order-contract.mjs';
const clone = x => JSON.parse(JSON.stringify(x));
const requireValue = x => { if (!x) throw new Error('invalid_paper_order_detail'); };
const canonical = x => JSON.stringify(Object.keys(x).sort().map(k => [k, x[k]]));
function normalize(c, p) {
  if (c === 0n) return [0n, 0];
  while (c % 10n === 0n) { c /= 10n; p++; }
  return [c, p];
}
function scalar(value) {
  requireValue(typeof value === 'string' && value.length > 0 && value.length <= 128);
  const m = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,4}))?$/.exec(value);
  requireValue(m);
  const digits = m[2] + (m[3] || ''), power = Number(m[4] || '0') - (m[3] || '').length;
  requireValue(power >= -1000 && power <= 1000 && (m[1] !== '-' || !/[1-9]/.test(digits)));
  return normalize(BigInt(digits), power);
}
function same(a, b) { return a[0] === b[0] && a[1] === b[1]; }
function greater(a, b) {
  const p = Math.min(a[1], b[1]);
  return a[0] * 10n ** BigInt(a[1] - p) > b[0] * 10n ** BigInt(b[1] - p);
}
function product28(a, b) {
  let c = a[0] * b[0], p = a[1] + b[1];
  const excess = c.toString().length - 28;
  if (excess > 0) {
    const factor = 10n ** BigInt(excess), remainder = c % factor;
    c /= factor;
    if (remainder * 2n > factor || (remainder * 2n === factor && c % 2n !== 0n)) c++;
    p += excess;
  }
  return normalize(c, p);
}

export function validateDetail(r, context) {
  requireValue(r && context && r.schema_version === 1 && r.source === 'durable_paper_order_detail');
  requireValue(r.mode === 'paper' && r.read_only === true && r.live_enabled === false);
  for (const k of ['account_id', 'session_version', 'revision', 'digest']) requireValue(r[k] === context[k]);
  stamp(r.observed_at);
  requireValue(r.order && canonical(r.order) === canonical(context.order));
  const o = r.order, f = r.fill, e = r.evidence, confirmed = o.status === 'filled';
  requireValue(['USDT', 'KRW'].includes(r.quote_currency) && o.symbol.split('/')[1] === r.quote_currency);
  requireValue((f !== null) === o.account_fill_present);
  if (f !== null) {
    requireValue(f && identifier(f.venue_order_id));
    if (o.exchange_order_id !== null) requireValue(f.venue_order_id === o.exchange_order_id);
    requireValue(units(f.quantity) === units(o.amount));
    const price = scalar(f.average_price), notional = scalar(f.notional), fee = scalar(f.fee_amount);
    requireValue(price[0] > 0n && same(notional, product28(scalar(f.quantity), price)));
    requireValue(!greater(fee, notional) && f.fee_currency === r.quote_currency);
    requireValue(Number.isSafeInteger(f.timestamp_ms) && f.timestamp_ms >= 0 && Number.isFinite(new Date(f.timestamp_ms).getTime()));
  }
  const category = confirmed ? 'confirmed_fill' : f !== null ? 'unconfirmed_account_fill' : 'unresolved_without_account_fill';
  requireValue(e && e.classification === category && e.cause === 'not_recorded');
  requireValue(e.requires_reconciliation === !confirmed && e.may_resubmit === false);
  return r;
}

export function createOrderDetailState() {
  let sequence = 0, active = null, context = null, report = null, reason = null;
  return {
    begin(page, clientId) {
      validatePage(page);
      const order = page.orders.items.find(o => o.client_order_id === clientId);
      requireValue(order);
      context = clone({account_id: page.account_id, session_version: page.session_version,
        revision: page.revision, digest: page.digest, order});
      report = null; reason = null;
      active = ++sequence;
      const q = new URLSearchParams({client_id: clientId, revision: page.revision, digest: page.digest});
      return {id: active, url: '/api/paper-session/order?' + q.toString()};
    },
    accept(id, value) {
      if (active !== id) return false;
      try { validateDetail(value, context); report = clone(value); active = null; return true; }
      catch (error) { active = null; reason = 'unavailable'; report = null; throw error; }
    },
    fail(id, why='unavailable') {
      if (active !== id) return false;
      requireValue(['unavailable', 'changed', 'not_found'].includes(why));
      active = null; report = null; reason = why; return true;
    },
    clear() { active = null; context = null; report = null; reason = null; },
    view() { return clone({context, report, reason, busy: active !== null}); }
  };
}

const messages = {
  confirmed_fill: '주문 응답과 계좌의 모의 체결 기록이 일치합니다. 실제 거래소 확인이나 새 주문 승인을 뜻하지 않습니다.',
  unconfirmed_account_fill: '계좌에는 체결이 저장됐지만 주문 응답은 미확인입니다. 같은 주문을 다시 보내지 말고, 소유 프로세스에서 기존 주문 조회로 확인해야 합니다.',
  unresolved_without_account_fill: '이 저장본에는 계좌 체결 근거가 없습니다. 미접수·거절을 뜻하지 않으며, 자동 재주문을 허용하지 않습니다.'
};
const statuses = {filled: '체결 확인', unknown: '결과 미확인', submitting: '전송 의도 저장'};

export function mountOrderDetail(doc, onChanged=()=>{}) {
  const state = createOrderDetailState(), byId = id => doc.getElementById(id);
  let controller = null, returnFocus = null;
  const set = (id, text) => { byId(id).textContent = text; };
  function fields(id, pairs) {
    const nodes = [];
    for (const [label, value] of pairs) {
      const dt = doc.createElement('dt'), dd = doc.createElement('dd');
      dt.textContent = label; dd.textContent = value; dd.title = value; nodes.push(dt, dd);
    }
    byId(id).replaceChildren(...nodes);
  }
  function draw() {
    const v = state.view(), r = v.report;
    byId('order-detail').hidden = v.context === null;
    byId('detail-values').hidden = r === null;
    set('detail-provenance', '');
    byId('order-detail').dataset.warning = String(v.reason !== null || (r !== null && r.evidence.requires_reconciliation));
    if (!r) {
      fields('detail-order-fields', []); fields('detail-fill-fields', []);
      set('detail-status', v.busy ? '같은 저장 시점의 상세 조회 중…' : v.reason ? '상세 확인 불가' : '선택 대기');
      set('detail-message', v.reason === 'changed' ? '목록 이후 계좌가 변경됐습니다. 최신 기록으로 처음부터 다시 조회하세요.'
        : v.reason === 'not_found' ? '같은 저장본에서 해당 주문을 확인하지 못했습니다. 원본을 점검하고 목록을 다시 조회하세요.'
        : v.reason ? '상세 기록을 확인하지 못했습니다. 잔고나 주문 상태를 추정하지 않습니다.' : '검증된 저장 기록만 표시합니다.');
      return;
    }
    set('detail-status', '목록과 동일한 저장본 · 읽기 전용');
    set('detail-message', messages[r.evidence.classification]);
    const o = r.order, f = r.fill;
    fields('detail-order-fields', [['고객 주문 ID', o.client_order_id], ['종목 · 방향', `${o.symbol} · ${o.side === 'buy' ? '매수' : '매도'}`],
      ['저장 상태', statuses[o.status]], ['주문 수량', o.amount], ['응답 확인 수량', o.filled], ['응답 기준 잔량', o.remaining],
      ['응답의 주문번호', o.exchange_order_id ?? '미확인'], ['전략 ID', o.strategy_id ?? '미설정'],
      ['로컬 생성 시각', o.created_at], ['로컬 갱신 시각', o.updated_at], ['미확인 원인', '원인 미기록 · 추정하지 않음']]);
    fields('detail-fill-fields', f === null ? [['체결 근거', '이 저장본에 없음'], ['가격 · 수수료', '미확인 — 0으로 간주하지 않음']]
      : [['계좌의 주문번호', f.venue_order_id], ['체결 수량', f.quantity], ['모의 체결가격', `${f.average_price} ${r.quote_currency}`],
         ['모의 체결금액', `${f.notional} ${r.quote_currency}`], ['기록된 수수료', `${f.fee_amount} ${f.fee_currency}`],
         ['입력 시세의 시각', new Date(f.timestamp_ms).toISOString()], ['원본 밀리초', String(f.timestamp_ms)],
         ['금액 해석', '체결금액은 손익·현금 변화량이 아닙니다. 수수료를 계좌에서 다시 차감하지 않습니다.']]);
    set('detail-provenance', `리비전 ${r.revision} · 내용 식별값 ${r.digest} · 상세 조회 ${r.observed_at}`);
  }
  function clear() { controller?.abort(); controller = null; state.clear(); draw(); }
  byId('detail-close').addEventListener('click', () => { clear(); if (returnFocus?.isConnected) returnFocus.focus(); });
  return {
    clear,
    async select(page, clientId, trigger=null) {
      controller?.abort();
      let req;
      try { req = state.begin(page, clientId); } catch { clear(); return; }
      returnFocus = trigger;
      const localController = new AbortController(); controller = localController;
      draw(); byId('order-detail').focus(); byId('order-detail').scrollIntoView({block:'nearest'});
      const timer = setTimeout(() => { state.fail(req.id); localController.abort(); draw(); }, 10000);
      try {
        const response = await fetch(req.url, {cache:'no-store', credentials:'same-origin', signal:localController.signal});
        if (response.status === 409) { if (state.fail(req.id, 'changed')) onChanged(); }
        else if (response.status === 404) state.fail(req.id, 'not_found');
        else if (!response.ok) state.fail(req.id);
        else state.accept(req.id, await response.json());
      } catch { state.fail(req.id); }
      finally { clearTimeout(timer); if (controller === localController) controller = null; draw(); }
    }
  };
}
