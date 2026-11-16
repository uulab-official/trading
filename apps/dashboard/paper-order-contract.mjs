/* Read-only order pages. Stored status is not current venue evidence. */
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


export {FILTERS, count, identifier, revision, units, stamp};
