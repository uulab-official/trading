import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';

const moduleUrl = new URL('../apps/dashboard/market-data.mjs', import.meta.url);
async function api() {
  assert.ok(existsSync(moduleUrl), 'market capture client is missing');
  return import(moduleUrl);
}
const end = Date.UTC(2026, 10, 14, 12, 1);
function report() {
  return {
    schema_version: 1, source: 'upbit_market_capture', mode: 'paper', read_only: true,
    live_enabled: false, scope: 'closed_public_candle_observation',
    observed_at: '2026-11-14T12:01:10.123456+00:00', status: 'current',
    configuration: {market: 'KRW-BTC', unit_minutes: 1, bootstrap_bars: 2, retention_bars: 3, poll_seconds: 15},
    capture: {capture_id: 'a'.repeat(32), revision: 2, phase: 'running',
      created_at: '2026-11-14T12:01:05.000000+00:00', updated_at: '2026-11-14T12:01:06.123456+00:00',
      last_attempt_at: '2026-11-14T12:01:05.000000+00:00', last_verified_at: '2026-11-14T12:01:06.123456+00:00',
      last_error: null, consecutive_failures: 0, successful_polls: 1, accepted_bars_total: 2, evicted_bars_total: 0},
    window: {bars: 2, first_open_ms: end - 120000, end_exclusive_ms: end, candles_sha256: 'b'.repeat(64)},
    freshness: {data_age_ms: 10123, observation_age_ms: 4000, max_data_age_ms: 90000,
      max_observation_age_ms: 30000, clock_valid: true},
    latest: {open_ms: end - 60000, close_ms: end, open: '9007199254740993.123456789012345678',
      high: '9007199254740994', low: '9007199254740992', close: '9007199254740993.000000000000000001', volume: '0.000000000000000001'},
    boundaries: {orders: false, account_access: false, process_liveness: 'not_asserted',
      provider_authenticity: 'not_authenticated', gap_policy: 'reject', retention: 'rolling_window'}
  };
}
function starting() {
  const r = report(); r.status = 'starting';
  Object.assign(r.capture, {revision: 0, phase: 'starting', updated_at: r.capture.created_at,
    last_attempt_at: null, last_verified_at: null, successful_polls: 0, accepted_bars_total: 0});
  r.window = {bars: 0, first_open_ms: null, end_exclusive_ms: null, candles_sha256: null};
  r.freshness.data_age_ms = null; r.freshness.observation_age_ms = null; r.latest = null;
  return r;
}
function response(value = report(), options = {}) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json; charset=utf-8'}, ...options});
}
function deferred() {
  let resolve; const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function timers() {
  let sequence = 0; const jobs = new Map();
  return {
    setTimer(fn, delay) {const id = ++sequence; jobs.set(id, {fn, delay}); return id;},
    clearTimer(id) {jobs.delete(id);},
    fire(delay) {
      const entry = [...jobs].find(([, job]) => job.delay === delay);
      assert.ok(entry, `no pending ${delay} ms timer`);
      jobs.delete(entry[0]); entry[1].fn();
    },
    has(delay) {return [...jobs.values()].some(job => job.delay === delay);}
  };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('exact capture schema preserves microsecond times and all decimal digits', async () => {
  const {validateReport, formatDecimal} = await api(); const r = report();
  assert.equal(validateReport(r), r);
  assert.equal(formatDecimal(r.latest.close), '9,007,199,254,740,993.000000000000000001');
  assert.equal(formatDecimal(r.latest.volume), '0.000000000000000001');
  assert.equal(formatDecimal('0'), '0');
  assert.equal(formatDecimal(null), '—');
});

test('a valid empty capture remains empty, never a zero-priced current candle', async () => {
  const {validateReport} = await api(); const r = starting();
  assert.equal(validateReport(r).latest, null);
  r.status = 'current'; assert.throws(() => validateReport(r));
});

test('schema drift, write flags and asserted liveness fail closed', async () => {
  const {validateReport} = await api();
  const mutations = [r => r.schema_version = 2, r => r.mode = 'live', r => r.read_only = false,
    r => r.live_enabled = true, r => r.scope = 'order_authorization', r => r.source = 'other',
    r => r.extra = true, r => delete r.capture.revision, r => r.configuration.extra = 1,
    r => r.boundaries.orders = true, r => r.boundaries.account_access = true,
    r => r.boundaries.process_liveness = 'alive', r => r.boundaries.provider_authenticity = 'authenticated',
    r => r.boundaries.gap_policy = 'fill', r => r.boundaries.retention = 'archive'];
  for (const mutate of mutations) {const r = report(); mutate(r); assert.throws(() => validateReport(r));}
});

test('invalid configuration and unsafe counters cannot reach the display', async () => {
  const {validateReport} = await api();
  const mutations = [r => r.configuration.market = 'KRW-KRW', r => r.configuration.market = '<img src=x>',
    r => r.configuration.unit_minutes = 2, r => r.configuration.bootstrap_bars = 0,
    r => r.configuration.retention_bars = 1, r => r.configuration.retention_bars = 5001,
    r => r.configuration.poll_seconds = 4, r => r.capture.revision = Number.MAX_SAFE_INTEGER + 1,
    r => r.capture.consecutive_failures = -1, r => r.capture.capture_id = 'x'.repeat(32),
    r => r.capture.evicted_bars_total = 1, r => r.window.bars = 4];
  for (const mutate of mutations) {const r = report(); mutate(r); assert.throws(() => validateReport(r));}
});

test('malformed, noncanonical and impossible OHLC values are rejected', async () => {
  const {validateReport} = await api();
  for (const value of ['NaN', 'Infinity', '-1', '1e3', '01', '1.0', '0', 12,
    '1000000000000000000.000000000000000001', '0.0000000000000000001']) {
    const r = report(); r.latest.open = value; assert.throws(() => validateReport(r));
  }
  for (const mutate of [r => r.latest.high = '1', r => r.latest.low = '9999999999999999',
    r => r.latest.volume = '-0.1', r => r.latest.close_ms += 1,
    r => r.window.first_open_ms += 60000, r => r.window.candles_sha256 = 'bad']) {
    const r = report(); mutate(r); assert.throws(() => validateReport(r));
  }
});

test('UTC chronology is checked without accepting normalized dates or lost microseconds', async () => {
  const {validateReport} = await api();
  for (const value of ['2026-02-30T12:01:10+00:00', '2026-11-14T24:01:10+00:00',
    '2026-11-14T12:01:10', '2026-11-14T12:01:10+09:00', '2026-11-14T12:01:10.1234567Z']) {
    const r = report(); r.observed_at = value; assert.throws(() => validateReport(r));
  }
  const future = report(); future.capture.updated_at = '2026-11-14T12:01:10.123457+00:00';
  assert.throws(() => validateReport(future));
});

test('freshness ages, thresholds and current claims must agree with the saved observation', async () => {
  const {validateReport} = await api();
  for (const mutate of [r => r.freshness.data_age_ms = 0, r => r.freshness.observation_age_ms = null,
    r => r.freshness.max_data_age_ms = 90001, r => r.freshness.max_observation_age_ms = 30001,
    r => r.status = 'stale', r => r.status = 'running', r => r.freshness.clock_valid = false]) {
    const r = report(); mutate(r); assert.throws(() => validateReport(r));
  }
  const stale = report(); stale.observed_at = '2026-11-14T12:02:40.123456+00:00';
  stale.freshness.data_age_ms = 100123; stale.freshness.observation_age_ms = 94000; stale.status = 'stale';
  assert.equal(validateReport(stale).status, 'stale');
  stale.status = 'current'; assert.throws(() => validateReport(stale));
});

test('saved stopped, retrying, blocked and fetching phases never become current', async () => {
  const {validateReport} = await api();
  for (const [phase, status, reason] of [['stopped', 'stopped', null], ['fetching', 'catching_up', null],
    ['retrying', 'retrying', 'rate_limited'], ['blocked', 'blocked', 'history_conflict']]) {
    const r = report(); Object.assign(r.capture, {phase, revision: 3, last_error: reason, consecutive_failures: reason ? 1 : 0});
    r.status = status; assert.equal(validateReport(r).status, status);
    r.status = 'current'; assert.throws(() => validateReport(r));
  }
});

test('clock errors preserve evidence with both ages unavailable', async () => {
  const {validateReport} = await api(); const r = report();
  r.observed_at = '2026-11-14T12:01:04+00:00'; r.status = 'clock_error';
  Object.assign(r.freshness, {clock_valid: false, data_age_ms: null, observation_age_ms: null});
  assert.equal(validateReport(r).capture.phase, 'running');
  r.freshness.data_age_ms = 0; assert.throws(() => validateReport(r));
});

test('failed refresh and invalid replacements retain the last good report with a stale warning', async () => {
  const {createMonitorState} = await api(); const state = createMonitorState(); state.accept(report()); state.fail();
  assert.equal(state.view().stale, true); assert.equal(state.view().report.latest.volume, '0.000000000000000001');
  const bad = report(); bad.live_enabled = true; assert.throws(() => state.accept(bad));
  assert.equal(state.view().report.live_enabled, false); state.accept(report()); assert.equal(state.view().stale, false);
});

test('capture replacement, configuration changes and revision rollback cannot silently replace a report', async () => {
  const {createMonitorState} = await api();
  for (const mutate of [r => r.capture.capture_id = 'c'.repeat(32), r => r.capture.revision = 1,
    r => r.configuration.retention_bars = 4, r => r.window.candles_sha256 = 'c'.repeat(64),
    r => r.latest.volume = '2']) {
    const state = createMonitorState(); state.accept(report()); const changed = report(); mutate(changed);
    assert.throws(() => state.accept(changed)); assert.equal(state.view().report.window.candles_sha256, 'b'.repeat(64));
    assert.equal(state.view().stale, true);
  }
});

test('the same saved revision can age into stale without being mistaken for a history conflict', async () => {
  const {createMonitorState} = await api(); const state = createMonitorState(); state.accept(report());
  const later = report(); later.observed_at = '2026-11-14T12:02:40.123456+00:00';
  Object.assign(later.freshness, {data_age_ms: 100123, observation_age_ms: 94000}); later.status = 'stale';
  state.accept(later); assert.equal(state.view().report.status, 'stale'); assert.equal(state.view().stale, false);
});

test('accepted reports and view snapshots cannot mutate stored last good values', async () => {
  const {createMonitorState} = await api(); const state = createMonitorState(), r = report(); state.accept(r);
  r.latest.close = '2'; state.view().report.latest.close = '3';
  assert.equal(state.view().report.latest.close, '9007199254740993.000000000000000001');
});

test('overlapping refreshes share one fixed read-only request and accept its result once', async () => {
  const {createCaptureMonitor} = await api(); const pending = deferred(), clock = timers(); const calls = [];
  const monitor = createCaptureMonitor({...clock, fetcher: (url, options) => {calls.push({url, options}); return pending.promise;}});
  const first = monitor.refresh(), second = monitor.refresh(); assert.equal(first, second);
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/market-capture');
  assert.equal(calls[0].options.method, 'GET'); assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.cache, 'no-store'); assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.body, undefined); assert.equal(monitor.view().busy, true);
  pending.resolve(response()); assert.equal(await first, true); assert.equal(monitor.view().busy, false);
  assert.equal(monitor.view().report.status, 'current'); monitor.stop();
});

test('unavailable, non-JSON and malformed responses preserve previous good data', async () => {
  const {createCaptureMonitor} = await api(); const queue = [response(), response({error: 'market_capture_unavailable'}, {status: 503}),
    response(report(), {headers: {'Content-Type': 'text/html'}}), new Response('{', {headers: {'Content-Type': 'application/json'}})];
  const monitor = createCaptureMonitor({...timers(), fetcher: async () => queue.shift()});
  assert.equal(await monitor.refresh(), true);
  for (let i = 0; i < 3; i++) {assert.equal(await monitor.refresh(), false); assert.equal(monitor.view().stale, true);
    assert.equal(monitor.view().report.capture.capture_id, 'a'.repeat(32));}
  monitor.stop();
});

test('oversized streamed data is rejected before an unbounded body can be accumulated', async () => {
  const {createCaptureMonitor} = await api(); let cancelled = false;
  const body = new ReadableStream({start(controller) {controller.enqueue(new Uint8Array(16385));}, cancel() {cancelled = true;}});
  const monitor = createCaptureMonitor({...timers(), fetcher: async () => new Response(body, {headers: {'Content-Type': 'application/json'}})});
  assert.equal(await monitor.refresh(), false); assert.equal(monitor.view().report, null); assert.equal(monitor.view().stale, true);
  assert.equal(cancelled, true); monitor.stop();
});

test('a deadline settles a stalled request and prevents its late reply from replacing newer data', async () => {
  const {createCaptureMonitor} = await api(); const late = deferred(), clock = timers(); let signal, calls = 0;
  const monitor = createCaptureMonitor({...clock, fetcher: async (_url, options) => {
    calls++; signal = options.signal; return calls === 1 ? late.promise : response();
  }});
  const first = monitor.refresh(); clock.fire(5000); assert.equal(await first, false);
  assert.equal(signal.aborted, true); assert.equal(monitor.view().busy, false); assert.equal(monitor.view().stale, true);
  assert.equal(await monitor.refresh(), true); late.resolve(response(starting())); await flush();
  assert.equal(monitor.view().report.capture.revision, 2); assert.equal(monitor.view().stale, false); monitor.stop();
});

test('the same deadline covers a response body that stalls after headers', async () => {
  const {createCaptureMonitor} = await api(); const clock = timers(); let cancelled = false;
  const body = new ReadableStream({start(controller) {controller.enqueue(new TextEncoder().encode('{'));}, cancel() {cancelled = true;}});
  const monitor = createCaptureMonitor({...clock, fetcher: async () => new Response(body, {headers: {'Content-Type': 'application/json'}})});
  const pending = monitor.refresh(); await flush(); clock.fire(5000); assert.equal(await pending, false);
  assert.equal(monitor.view().stale, true); assert.equal(cancelled, true); monitor.stop();
});

test('hidden tabs do not poll and visibility restoration makes one fresh request', async () => {
  const {createCaptureMonitor} = await api(); const clock = timers(); let visible = false, calls = 0;
  const monitor = createCaptureMonitor({...clock, visible: () => visible, fetcher: async () => {calls++; return response();}});
  await monitor.start(); assert.equal(calls, 0); assert.equal(monitor.view().paused, true); assert.equal(clock.has(10000), false);
  visible = true; await monitor.visibilityChanged(); assert.equal(calls, 1); assert.equal(clock.has(10000), true);
  visible = false; await monitor.visibilityChanged(); assert.equal(clock.has(10000), false);
  await monitor.refresh(); assert.equal(calls, 1); visible = true; await monitor.visibilityChanged(); assert.equal(calls, 2);
  monitor.stop(); assert.equal(clock.has(10000), false);
});

test('hiding an in-flight tab aborts and ignores the reply while retaining the last good report', async () => {
  const {createCaptureMonitor} = await api(); const clock = timers(), pending = deferred(); let visible = true, calls = 0, signal;
  const monitor = createCaptureMonitor({...clock, visible: () => visible, fetcher: async (_url, options) => {
    signal = options.signal; return ++calls === 1 ? response() : pending.promise;
  }});
  await monitor.start(); const request = monitor.refresh(); visible = false; await monitor.visibilityChanged();
  assert.equal(signal.aborted, true); assert.equal(await request, false); assert.equal(monitor.view().stale, true);
  pending.resolve(response(starting())); await flush(); assert.equal(monitor.view().report.capture.revision, 2); monitor.stop();
});

test('closing the page cancels work and makes late data unable to revive polling', async () => {
  const {createCaptureMonitor} = await api(); const clock = timers(), pending = deferred(); let calls = 0;
  const monitor = createCaptureMonitor({...clock, fetcher: async () => {calls++; return pending.promise;}});
  const request = monitor.start(); monitor.stop(); assert.equal(await request, false);
  pending.resolve(response()); await flush(); assert.equal(monitor.view().report, null);
  assert.equal(clock.has(10000), false); await monitor.refresh(); assert.equal(calls, 1);
});

test('an explicitly restored page resumes observation after pagehide without discarding last good data', async () => {
  const {createCaptureMonitor} = await api(); const clock = timers(); let calls = 0;
  const monitor = createCaptureMonitor({...clock, fetcher: async () => {calls++; return response();}});
  await monitor.start(); monitor.stop();
  assert.equal(await monitor.start(), true); assert.equal(calls, 2);
  assert.equal(monitor.view().report.capture.capture_id, 'a'.repeat(32));
  assert.equal(clock.has(10000), true); monitor.stop();
});

test('the renderer shows exact values and an explicit previous-observation warning using text nodes', async () => {
  const {renderObservation} = await api(); const elements = new Map(); const classes = new Set();
  const document = {body: {dataset: {}, classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name);}}},
    getElementById(id) {if (!elements.has(id)) elements.set(id, {textContent: '', hidden: false, title: '', disabled: false,
      set innerHTML(_) {throw new Error('HTML insertion is forbidden');}}); return elements.get(id);}};
  renderObservation(document, {report: report(), stale: false, busy: false, paused: false});
  assert.equal(elements.get('latest-close').textContent, '9,007,199,254,740,993.000000000000000001');
  assert.equal(elements.get('latest-close').title, '9007199254740993.000000000000000001');
  assert.equal(elements.get('refresh-warning').hidden, true);
  renderObservation(document, {report: report(), stale: true, busy: false, paused: false});
  assert.equal(elements.get('refresh-warning').hidden, false); assert.match(elements.get('status-label').textContent, /이전/);
  assert.equal(classes.has('stale'), true); assert.match(elements.get('data-age').textContent, /10/);
  assert.match(elements.get('observed-at').textContent, /2026-11-14/);
});

test('the shipped page has passive observation controls and a readable no-script fallback', async () => {
  await api(); const html = readFileSync(new URL('../apps/dashboard/market-data.html', import.meta.url), 'utf8');
  assert.equal((html.match(/<button\b/gi) || []).length, 1);
  assert.match(html, /<button[^>]+id="refresh"[^>]+type="button"/);
  assert.doesNotMatch(html, /<(?:form|input|iframe)\b|(?:src|href)="https?:/i);
  assert.match(html, /<noscript>/); assert.match(html, /id="refresh-warning"[^>]+role="alert"/);
});
