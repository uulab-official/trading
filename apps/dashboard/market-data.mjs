/* Public closed-candle observations. This page never starts collection or execution. */
const insist = value => {if (!value) throw new Error('invalid_market_capture_report');};
const clone = value => JSON.parse(JSON.stringify(value));
const phases = ['starting', 'fetching', 'running', 'retrying', 'blocked', 'stopped'];
const reasons = ['transport_unavailable', 'rate_limited', 'rate_blocked', 'request_rejected',
  'cooldown_required', 'data_rejected', 'history_conflict', 'catchup_limit', 'clock_regression'];
const retryable = ['transport_unavailable', 'rate_limited'];
const maxTimestamp = 253402300799999;
function exact(value, keys) {
  insist(value && typeof value === 'object' && !Array.isArray(value));
  const actual = Object.keys(value); insist(actual.length === keys.length && keys.every(key => Object.hasOwn(value, key)));
}
function integer(value, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  insist(Number.isSafeInteger(value) && value >= minimum && value <= maximum); return value;
}
function utc(value) {
  insist(typeof value === 'string');
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  insist(match);
  const base = Date.parse(match[1] + 'Z');
  insist(Number.isFinite(base) && base >= 0 && base <= maxTimestamp
    && new Date(base).toISOString().slice(0, 19) === match[1]);
  const micros = BigInt(base) * 1000n + BigInt((match[2] || '').padEnd(6, '0'));
  return {micros, ms: Number(micros / 1000n)};
}
function decimal(value, positive = false) {
  insist(typeof value === 'string' && /^(?:0|[1-9]\d{0,18})(?:\.\d{0,17}[1-9])?$/.test(value));
  const [whole, fraction = ''] = value.split('.');
  const scaled = BigInt(whole) * 10n ** 18n + BigInt(fraction.padEnd(18, '0'));
  insist(scaled <= 10n ** 36n && (!positive || scaled > 0n)); return scaled;
}
function same(left, right) {
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && same(left[key], right[key]));
}

export function validateReport(r) {
  exact(r, ['schema_version', 'source', 'mode', 'read_only', 'live_enabled', 'scope', 'observed_at',
    'status', 'configuration', 'capture', 'window', 'freshness', 'latest', 'boundaries']);
  insist(r.schema_version === 1 && r.source === 'upbit_market_capture' && r.mode === 'paper'
    && r.read_only === true && r.live_enabled === false && r.scope === 'closed_public_candle_observation');
  const observed = utc(r.observed_at), c = r.configuration, s = r.capture, w = r.window, f = r.freshness;
  exact(c, ['market', 'unit_minutes', 'bootstrap_bars', 'retention_bars', 'poll_seconds']);
  insist(typeof c.market === 'string' && /^KRW-[A-Z0-9]{1,20}$/.test(c.market) && c.market !== 'KRW-KRW');
  insist([1, 3, 5, 10, 15, 30, 60, 240].includes(c.unit_minutes));
  integer(c.bootstrap_bars, 1, 200); integer(c.retention_bars, c.bootstrap_bars, 5000); integer(c.poll_seconds, 5, 300);
  const duration = c.unit_minutes * 60000;
  exact(s, ['capture_id', 'revision', 'phase', 'created_at', 'updated_at', 'last_attempt_at', 'last_verified_at',
    'last_error', 'consecutive_failures', 'successful_polls', 'accepted_bars_total', 'evicted_bars_total']);
  insist(typeof s.capture_id === 'string' && /^[a-f0-9]{32}$/.test(s.capture_id) && phases.includes(s.phase));
  for (const key of ['revision', 'consecutive_failures', 'successful_polls', 'accepted_bars_total', 'evicted_bars_total']) integer(s[key]);
  insist(s.last_error === null || reasons.includes(s.last_error));
  insist((s.last_error === null) === (s.consecutive_failures === 0));
  insist(BigInt(s.successful_polls) + BigInt(s.consecutive_failures) <= BigInt(s.revision));
  const created = utc(s.created_at), updated = utc(s.updated_at);
  insist(created.micros <= updated.micros);
  const attempt = s.last_attempt_at === null ? null : utc(s.last_attempt_at);
  const verified = s.last_verified_at === null ? null : utc(s.last_verified_at);
  for (const stamp of [attempt, verified]) if (stamp) insist(stamp.micros >= created.micros && stamp.micros <= updated.micros);
  if (s.phase === 'starting') insist(s.revision === 0 && attempt === null);
  if (s.phase === 'fetching') insist(attempt !== null);
  if (['retrying', 'blocked'].includes(s.phase)) insist(s.last_error !== null);
  if (s.phase === 'retrying') insist(retryable.includes(s.last_error));
  exact(w, ['bars', 'first_open_ms', 'end_exclusive_ms', 'candles_sha256']);
  integer(w.bars, 0, c.retention_bars);
  insist(BigInt(s.accepted_bars_total) === BigInt(w.bars) + BigInt(s.evicted_bars_total));
  if (w.bars === 0) {
    insist(w.first_open_ms === null && w.end_exclusive_ms === null && w.candles_sha256 === null
      && r.latest === null && verified === null && s.successful_polls === 0 && s.accepted_bars_total === 0);
  } else {
    integer(w.first_open_ms, 0, maxTimestamp); integer(w.end_exclusive_ms, 1, maxTimestamp);
    insist(w.first_open_ms % duration === 0 && w.end_exclusive_ms - w.first_open_ms === w.bars * duration);
    insist(typeof w.candles_sha256 === 'string' && /^[a-f0-9]{64}$/.test(w.candles_sha256));
    insist(verified !== null && s.successful_polls > 0 && s.phase !== 'starting'
      && w.end_exclusive_ms <= verified.ms - 5000);
    const candle = r.latest;
    exact(candle, ['open_ms', 'close_ms', 'open', 'high', 'low', 'close', 'volume']);
    integer(candle.open_ms, 0, maxTimestamp); integer(candle.close_ms, 1, maxTimestamp);
    insist(candle.close_ms === w.end_exclusive_ms && candle.open_ms + duration === candle.close_ms);
    const open = decimal(candle.open, true), high = decimal(candle.high, true), low = decimal(candle.low, true), close = decimal(candle.close, true);
    decimal(candle.volume); insist(low <= open && open <= high && low <= close && close <= high);
  }
  if (s.phase === 'running') insist(w.bars > 0 && s.last_error === null);
  exact(f, ['data_age_ms', 'observation_age_ms', 'max_data_age_ms', 'max_observation_age_ms', 'clock_valid']);
  insist(f.max_data_age_ms === duration + 5000 + c.poll_seconds * 1000 + 10000);
  insist(f.max_observation_age_ms === Math.max(30000, 2 * c.poll_seconds * 1000));
  insist(typeof f.clock_valid === 'boolean');
  if (!f.clock_valid) {
    insist(r.status === 'clock_error' && f.data_age_ms === null && f.observation_age_ms === null);
  } else {
    for (const stamp of [created, updated, attempt, verified]) if (stamp) insist(stamp.micros <= observed.micros);
    if (w.bars) {
      integer(f.data_age_ms); integer(f.observation_age_ms);
      insist(f.data_age_ms === observed.ms - w.end_exclusive_ms && f.observation_age_ms === observed.ms - verified.ms);
    } else insist(f.data_age_ms === null && f.observation_age_ms === null);
    const status = s.phase === 'running'
      ? (w.bars > 0 && f.data_age_ms <= f.max_data_age_ms && f.observation_age_ms <= f.max_observation_age_ms ? 'current' : 'stale')
      : s.phase === 'fetching' ? 'catching_up' : s.phase;
    insist(r.status === status);
  }
  exact(r.boundaries, ['orders', 'account_access', 'process_liveness', 'provider_authenticity', 'gap_policy', 'retention']);
  insist(r.boundaries.orders === false && r.boundaries.account_access === false
    && r.boundaries.process_liveness === 'not_asserted' && r.boundaries.provider_authenticity === 'not_authenticated'
    && r.boundaries.gap_policy === 'reject' && r.boundaries.retention === 'rolling_window');
  return r;
}

export function formatDecimal(value) {
  if (value === null) return '—';
  decimal(value); const [whole, fraction] = value.split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : '.' + fraction);
}
function age(value) {
  if (value === null) return '미확인';
  if (value < 60000) return `${Math.floor(value / 1000)}${value % 1000 ? '.' + String(value % 1000).padStart(3, '0').replace(/0+$/, '') : ''}초`;
  const seconds = Math.floor(value / 1000);
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 ${Math.floor(seconds % 3600 / 60)}분`;
  return `${Math.floor(seconds / 86400)}일 ${Math.floor(seconds % 86400 / 3600)}시간`;
}
export function createMonitorState() {
  let report = null, stale = false;
  return {
    accept(value) {
      try {
        validateReport(value);
        if (report) {
          insist(value.capture.capture_id === report.capture.capture_id && same(value.configuration, report.configuration)
            && value.capture.created_at === report.capture.created_at && value.capture.revision >= report.capture.revision);
          if (value.capture.revision === report.capture.revision)
            for (const key of ['capture', 'window', 'latest']) insist(same(value[key], report[key]));
        }
        report = clone(value); stale = false;
      } catch (error) {stale = true; throw error;}
    },
    fail() {stale = true;},
    view() {return {report: report === null ? null : clone(report), stale};}
  };
}

async function readReport(fetcher, signal) {
  const response = await fetcher('/api/market-capture', {method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', signal});
  insist(response.status === 200 && response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() === 'application/json');
  const declared = response.headers.get('content-length');
  if (declared !== null) insist(/^\d{1,8}$/.test(declared) && Number(declared) <= 16384);
  insist(response.body && !signal.aborted);
  const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', {fatal: true});
  const cancel = () => {reader.cancel().catch(() => {});};
  signal.addEventListener('abort', cancel, {once: true});
  let size = 0, body = '';
  try {
    while (true) {
      const item = await reader.read(); insist(!signal.aborted);
      if (item.done) break;
      size += item.value.byteLength; insist(size <= 16384);
      body += decoder.decode(item.value, {stream: true});
    }
    body += decoder.decode(); return JSON.parse(body);
  } catch (error) {cancel(); throw error;}
  finally {signal.removeEventListener('abort', cancel); reader.releaseLock();}
}

export function createCaptureMonitor({fetcher = globalThis.fetch, visible = () => true,
  onChange = () => {}, setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout} = {}) {
  const state = createMonitorState();
  let active = null, polling = false, closed = false, next = null;
  const view = () => ({...state.view(), busy: active !== null, paused: !visible()});
  const notify = () => onChange(view());
  const clearNext = () => {if (next !== null) clearTimer(next); next = null;};
  function schedule() {
    clearNext();
    if (polling && !closed && visible() && active === null)
      next = setTimer(() => {next = null; refresh();}, 10000);
  }
  function refresh() {
    if (closed || !visible()) {notify(); return Promise.resolve(false);}
    if (active) return active.promise;
    clearNext();
    const request = {controller: new AbortController(), cancel: null, timer: null, promise: null};
    const cancelled = new Promise((_, reject) => {request.cancel = () => {
      request.controller.abort(); reject(new Error('market_capture_request_unavailable'));
    };});
    active = request;
    request.timer = setTimer(request.cancel, 5000);
    request.promise = (async () => {
      try {
        const result = await Promise.race([readReport(fetcher, request.controller.signal), cancelled]);
        if (active !== request || closed || !visible() || request.controller.signal.aborted) return false;
        state.accept(result); return true;
      } catch {
        request.controller.abort(); if (active === request && !closed) state.fail(); return false;
      } finally {
        clearTimer(request.timer); if (active === request) active = null;
        notify(); schedule();
      }
    })();
    notify(); return request.promise;
  }
  return {
    refresh, view,
    start() {
      if (closed) {closed = false; if (state.view().report) state.fail();}
      polling = true;
      return active?.controller.signal.aborted ? active.promise.then(refresh) : refresh();
    },
    visibilityChanged() {
      clearNext();
      if (!visible()) {if (active) active.cancel(); notify(); return active?.promise || Promise.resolve(false);}
      if (polling && !closed) return active?.controller.signal.aborted ? active.promise.then(refresh) : refresh();
      notify(); return Promise.resolve(false);
    },
    stop() {closed = true; polling = false; clearNext(); if (active) active.cancel();}
  };
}

const statusLabels = {starting: '첫 수집 대기', catching_up: '수집 시도 저장됨', current: '관측 시점에 최신',
  stale: '허용 경과시간 초과', retrying: '재시도 대기 저장됨', blocked: '수집 차단 저장됨', stopped: '중지 상태 저장됨', clock_error: '시각 일관성 확인 필요'};
const phaseLabels = {starting: '시작 대기', fetching: '수집 중 상태', running: '수집 성공 상태', retrying: '재시도 상태', blocked: '차단 상태', stopped: '중지 상태'};
const reasonLabels = {transport_unavailable: '공개 데이터 연결 실패', rate_limited: '요청 제한 · 재시도 대기',
  rate_blocked: '제공자 요청 차단', request_rejected: '요청 거부', cooldown_required: '대기 정책 확인 필요',
  data_rejected: '데이터 검증 실패', history_conflict: '기존 캔들과 충돌', catchup_limit: '따라잡기 범위 초과', clock_regression: '시계 역행'};
const statusNotes = {
  starting: '아직 검증하여 저장한 캔들이 없습니다. 저장 단계는 수집 프로세스의 실행 여부를 증명하지 않습니다.',
  catching_up: '수집 시도가 저장되었습니다. 비정상 종료 후에도 이 단계가 남을 수 있습니다. 실제 프로세스와 운영 기록을 확인하세요.',
  stale: '캔들 종료 시각 또는 마지막 검증 시각이 허용 범위를 벗어났습니다. 이전에 저장한 값과 시각을 함께 확인하세요.',
  retrying: '일시 오류와 재시도 대기가 기록되어 있습니다. 재시도가 실제로 실행 중인지는 이 화면에서 확인할 수 없습니다.',
  blocked: '수집이 차단된 기록입니다. 실패 원인을 확인해야 하며, 화면에서 수집을 다시 시작하지 않습니다.',
  stopped: '수집 중지가 저장되었습니다. 보존한 창은 과거 자료로 사용할 수 있으며, 현재 프로세스 상태를 뜻하지 않습니다.',
  clock_error: '시각의 순서가 일치하지 않아 경과시간을 판단할 수 없습니다. 저장한 캔들과 오류 증거는 그대로 표시합니다.'
};
export function renderObservation(document, view) {
  const {report: r, stale, busy, paused} = view;
  const byId = id => document.getElementById(id);
  const set = (id, value, title = null) => {const element = byId(id); element.textContent = value; if (title !== null) element.title = title;};
  document.body.classList.toggle('stale', stale);
  document.body.dataset.captureStatus = stale ? 'unavailable' : r?.status || 'unavailable';
  byId('refresh').disabled = busy || paused;
  byId('refresh-warning').hidden = !stale;
  set('refresh-warning', r ? '현재 상태를 확인하지 못했습니다. 아래 값은 마지막 정상 조회 결과이며 현재의 최신 상태를 보장하지 않습니다.'
    : '수집 상태를 읽을 수 없습니다. 관측 서버의 수집 경로 설정과 파일 상태를 확인하세요. 아직 표시할 정상 조회 결과가 없습니다.');
  set('request-status', paused ? '탭 숨김 · 자동 확인 일시 정지' : busy ? '저장 상태 확인 중…'
    : stale ? '조회 실패 · 이전 결과 보존' : r ? '조회 완료 · 10초 후 다시 확인' : '조회 대기');
  if (!r) return;
  set('status-label', (stale ? '이전 조회: ' : '') + statusLabels[r.status]);
  set('capture-phase', `저장 단계: ${phaseLabels[r.capture.phase]} (${r.capture.phase})`);
  set('market-label', `${r.configuration.market} · ${r.configuration.unit_minutes}분 캔들`);
  byId('state-warning').hidden = r.status === 'current';
  set('state-warning', statusNotes[r.status] || '');
  set('failure-reason', r.capture.last_error ? `${reasonLabels[r.capture.last_error]} · ${r.capture.last_error}` : '저장된 실패 사유 없음');
  for (const [id, value] of [['data-age', r.freshness.data_age_ms], ['observation-age', r.freshness.observation_age_ms]])
    set(id, age(value), value === null ? '경과시간 미확인' : `${value} ms · API 관측 시점 기준`);
  set('data-budget', `허용 ${age(r.freshness.max_data_age_ms)} · 관측 시점 기준`);
  set('observation-budget', `허용 ${age(r.freshness.max_observation_age_ms)} · 관측 시점 기준`);
  set('window-bars', `${r.window.bars} / ${r.configuration.retention_bars}`);
  set('window-policy', `누적 수락 ${r.capture.accepted_bars_total}개 · 보존 창 밖 ${r.capture.evicted_bars_total}개`);
  set('latest-close', formatDecimal(r.latest?.close ?? null), r.latest?.close ?? '저장된 캔들 없음');
  byId('no-candles').hidden = r.latest !== null; byId('candle-values').hidden = r.latest === null;
  for (const key of ['open', 'high', 'low', 'close', 'volume'])
    set(`candle-${key}`, formatDecimal(r.latest?.[key] ?? null), r.latest?.[key] ?? '저장된 캔들 없음');
  const stamp = value => value === null ? '저장된 시각 없음' : new Date(value).toISOString();
  set('candle-period', r.latest ? `${stamp(r.latest.open_ms)} → ${stamp(r.latest.close_ms)} (종료 경계)` : '저장된 닫힌 캔들 없음');
  set('window-first', stamp(r.window.first_open_ms)); set('window-end', stamp(r.window.end_exclusive_ms));
  set('capture-id', r.capture.capture_id); set('revision', String(r.capture.revision));
  set('created-at', r.capture.created_at); set('updated-at', r.capture.updated_at);
  set('last-attempt', r.capture.last_attempt_at ?? '시도 기록 없음');
  set('last-verified', r.capture.last_verified_at ?? '검증 기록 없음'); set('observed-at', r.observed_at);
  set('poll-count', String(r.capture.successful_polls)); set('failure-count', String(r.capture.consecutive_failures));
  set('poll-interval', `${r.configuration.poll_seconds}초`); set('bootstrap', `${r.configuration.bootstrap_bars}개`);
  set('candles-hash', r.window.candles_sha256 ?? '저장된 캔들 없음');
}

function mount() {
  const monitor = createCaptureMonitor({visible: () => document.visibilityState === 'visible',
    onChange: view => renderObservation(document, view)});
  document.getElementById('refresh').addEventListener('click', () => {monitor.refresh();});
  document.addEventListener('visibilitychange', () => {monitor.visibilityChanged();});
  window.addEventListener('pagehide', () => {monitor.stop();});
  window.addEventListener('pageshow', event => {if (event.persisted) monitor.start();});
  monitor.start();
}
if (typeof document !== 'undefined' && document.getElementById('market-capture-observer')) mount();
