"""Pure presentation of a read-only market study inspection.

Values come from the checker verbatim. This module never opens study files,
verifies receipts, computes returns, or authorizes account operations.
"""
from html import escape
import json


_UNAVAILABLE = '정보 없음'
_ROW_STATUS = {
    'verified_completion': ('개별 검증 완료', 'ok'),
    'not_started': ('시작 전', 'muted'),
    'incomplete': ('준비 미완료', 'pending'),
    'compatible_snapshot': ('실행 미완료', 'pending'),
    'reconciliation_required': ('대조 확인 필요', 'pending'),
    'blocked': ('검사 차단', 'blocked'),
    'unavailable': ('확인 불가', 'blocked'),
}
_COMMON = (
    ('initial_balance', '초기 자본'),
    ('position_size_pct', '포지션 비중'),
    ('fee_pct', '수수료율'),
    ('slippage_pct', '슬리피지율'),
    ('max_daily_loss_pct', '일일 손실 제한'),
    ('max_drawdown_pct', '낙폭 제한 설정'),
)
_CSS = """
:root{color-scheme:light;--ink:#182331;--sub:#536276;--line:#dce3eb;--paper:#fff;
--bg:#f4f6f9;--blue:#315fe8;--green:#12644e;--amber:#855510;--red:#a02c37}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:13px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
main{width:min(1200px,100%);margin:auto;padding:32px 28px 40px}h1,h2,h3,p,dl{margin:0}
h1{font-size:27px;line-height:1.35;letter-spacing:-1.1px;font-weight:750}h2{font-size:16px;
letter-spacing:-.35px}h3{font-size:15px;letter-spacing:-.2px;overflow-wrap:anywhere}
.eyebrow{font-size:10px;font-weight:750;letter-spacing:1.7px;color:var(--blue);margin-bottom:8px}
.intro{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:20px}
.subtitle{color:var(--sub);margin-top:8px;font-size:12px}.readonly{background:#eaf0ff;color:#3153a5;
border:1px solid #dbe5fc;border-radius:6px;padding:4px 9px;white-space:nowrap;font-size:11px}
.panel,.candidate{background:var(--paper);border:1px solid var(--line);border-radius:10px}
.overview{padding:19px 20px;margin-bottom:15px}.status-line{display:flex;gap:12px;
align-items:center;justify-content:space-between;margin-bottom:14px}.badge{display:inline-block;
font-size:11px;font-weight:700;padding:3px 8px;border-radius:5px;white-space:nowrap}
.ok{background:#e6f4ee;color:var(--green)}.pending{background:#fff3d9;color:var(--amber)}
.blocked{background:#fbeaec;color:var(--red)}.muted{background:#edf0f4;color:#526071}
.receipt{font-size:11px;color:var(--sub)}.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
gap:14px 20px}.facts>div,.assumptions>div,.metrics>div{min-width:0}dt{color:var(--sub);font-size:11px}
dd{margin:3px 0 0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums;font-weight:550}
.observed{font-size:11px;color:var(--sub);margin-top:14px;border-top:1px solid #edf0f4;padding-top:11px;
overflow-wrap:anywhere}.assumption-panel{padding:17px 20px;margin-bottom:24px}.section-note{
color:var(--sub);font-size:11px;margin-top:3px}.assumptions{display:grid;
grid-template-columns:repeat(6,minmax(0,1fr));gap:16px;margin-top:13px}.section-heading{display:flex;
align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:10px}.section-heading p{
font-size:11px;color:var(--sub)}.candidate{padding:17px 20px;margin-bottom:10px}.candidate-head{
display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.identity{display:flex;
flex-wrap:wrap;align-items:center;gap:8px}.baseline{font-size:10px;font-weight:650;color:#3c58a6;
background:#edf1fd;padding:1px 6px;border-radius:4px}.strategy{margin-top:4px;color:var(--sub);font-size:11px;
overflow-wrap:anywhere}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
gap:12px 24px;margin-top:14px;padding-top:13px;border-top:1px solid #edf0f4}.metrics dd{
font-size:12px}.metrics .primary dd{font-size:15px;font-weight:700;letter-spacing:-.3px}
.risk{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:11px;color:var(--sub)}
.risk span{overflow-wrap:anywhere}.unavailable{color:#697587;font-weight:400}
details{font-size:11px;color:var(--sub)}.candidate details{margin-top:11px}
summary{cursor:pointer;width:fit-content;max-width:100%;padding:3px 0;font-weight:600}
summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px;border-radius:2px}
.diagnostics{margin-top:8px;border-top:1px solid #edf0f4;padding-top:8px;display:grid;
grid-template-columns:130px minmax(0,1fr);gap:4px 12px}.diagnostics dd{font-size:11px;font-weight:400;
white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.diagnostics dt{overflow-wrap:anywhere}
.notice{background:#eef2f8;border:1px solid var(--line);border-radius:8px;padding:13px 16px;
margin-top:17px;color:#4b5b70;font-size:11px}.notice p+p{margin-top:4px}.provenance{margin-top:16px}
.empty{padding:28px 20px;color:var(--sub);text-align:center}.footer{margin-top:17px;font-size:10px;
color:#6b7788}.delta-note{font-size:11px;color:var(--sub);margin:9px 0 14px}
@media(min-width:1200px){main{padding-top:38px}.metrics{grid-template-columns:1.35fr 1.1fr 1.1fr 1fr}}
@media(max-width:700px){main{padding:22px 15px 30px}h1{font-size:23px}.intro{gap:10px}
.readonly{font-size:10px;padding:3px 6px}.overview,.assumption-panel,.candidate{padding:15px}
.facts{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.assumptions{grid-template-columns:
repeat(2,minmax(0,1fr));gap:12px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.metrics .primary dd{font-size:14px}.status-line{align-items:flex-start;gap:8px;flex-direction:column}
.section-heading{display:block}.section-heading p{margin-top:3px}.candidate-head{gap:8px}
.diagnostics{grid-template-columns:1fr;gap:1px}.diagnostics dd{margin-bottom:7px}.risk{gap:4px 14px}}
@media print{body{background:#fff}main{width:100%;padding:0}.panel,.candidate{break-inside:avoid}
summary{color:#182331}.notice{background:#fff}}
"""


def _text(value):
    if value is None or value == '':
        return _UNAVAILABLE
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _e(value):
    return escape(_text(value), quote=True)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _value(value, unit=''):
    """Preserve the checker's representation, including zero and precision."""
    if value is None or value == '':
        return '<span class="unavailable">'+_UNAVAILABLE+'</span>'
    return _e(value) + (' '+_e(unit) if unit else '')


def _field(key, label, value, unit='', primary=False):
    return ('<div'+(' class="primary"' if primary else '')+'><dt>'+escape(label)+'</dt>'
            '<dd data-field="'+key+'">'+_value(value, unit)+'</dd></div>')


def _diagnostics(entries):
    return '<dl class="diagnostics">'+''.join(
        '<dt>'+escape(label)+'</dt><dd>'+_e(value)+'</dd>' for label, value in entries)+'</dl>'


def _row(row, quote, baseline, show_delta):
    status = row.get('status')
    label, tone = _ROW_STATUS.get(status, ('알 수 없는 상태', 'muted')) if isinstance(status, str) else (
        '알 수 없는 상태', 'muted')
    verified = status == 'verified_completion'
    metrics = _mapping(row.get('metrics')) if verified else {}
    config = _mapping(row.get('config'))
    strategy = config.get('strategy')
    strategy_label = {'ma_cross': '이동평균 교차', 'rsi': 'RSI'}.get(strategy, _text(strategy)) if isinstance(
        strategy, str) else _text(strategy)
    parameters = []
    if strategy == 'ma_cross':
        parameters = [('빠른 기간', config.get('fast_period')), ('느린 기간', config.get('slow_period'))]
    elif strategy == 'rsi':
        parameters = [('RSI 기간', config.get('rsi_period'))]
    strategy_text = _e(strategy_label)+''.join(' · '+escape(name)+' '+_e(value) for name, value in parameters)
    identifier = row.get('id')
    baseline_mark = '<span class="baseline">기준 후보</span>' if baseline is not None and identifier == baseline else ''
    fields = (
        ('equity', '평가자산', quote, True), ('return_pct', '수익률', '%', True),
        ('cash', '현금', quote, False), ('fees', '누적 수수료', quote, False),
        ('fills', '체결 수', '건', False), ('bars', '처리 봉 수', '개', False),
    )
    rendered = ''.join(_field(key, title, metrics.get(key), unit, primary) for key, title, unit, primary in fields)
    rendered += _field('return_delta_pp', '기준 대비 수익률 차이',
                       row.get('return_delta_pp') if show_delta and verified else None, 'pp')
    flags = []
    for key, title in (('daily_loss_halted', '일일 손실 중지'), ('drawdown_halted', '낙폭 중지')):
        flag = metrics.get(key)
        text = '예' if flag is True else ('아니요' if flag is False else None)
        flags.append('<span>'+title+' · <strong data-field="'+key+'">'+_value(text)+'</strong></span>')
    provenance = [('상태 코드', status), ('진단 코드', row.get('reason')),
                  ('개별 관측 시각', row.get('observed_at')), ('실행 경로', row.get('run_directory')),
                  ('전략 설정', config)]
    if verified:
        provenance.extend((('결과 SHA-256', row.get('result_sha256')), ('입력 SHA-256', row.get('input_sha256')),
                           ('계정 스냅샷', row.get('snapshot'))))
    return ('<article class="candidate" data-candidate-id="'+_e(identifier)+'">'
            '<div class="candidate-head"><div><div class="identity"><h3>'+_e(identifier)+'</h3>'
            +baseline_mark+'</div><p class="strategy">'+strategy_text+'</p></div>'
            '<span class="badge '+tone+'">'+label+'</span></div><dl class="metrics">'+rendered+'</dl>'
            '<div class="risk">'+''.join(flags)+'</div><details><summary>후보 진단 및 설정</summary>'
            +_diagnostics(provenance)+'</details></article>')


def render_study_report(report: dict) -> str:
    """Render an existing v1 inspection without modifying or re-verifying it.

    An execution response is intentionally not a supported input. The overall
    badge and comparison deltas require every supplied verification signal.
    """
    if not (isinstance(report, dict)
            and type(report.get('schema_version')) is int and report['schema_version'] == 1
            and report.get('source') == 'market_paper_study' and report.get('mode') == 'paper'
            and report.get('read_only') is True and report.get('operation') == 'inspect_existing_study'
            and report.get('live_enabled') is False and report.get('resume_authorized') is False
            and all(isinstance(report.get(key), str) for key in ('status', 'reason', 'observed_at'))
            and type(report.get('receipt_validated')) is bool
            and type(report.get('all_candidates_verified')) is bool
            and isinstance(report.get('candidates'), list)
            and all(isinstance(row, dict) for row in report['candidates'])):
        raise ValueError('market_study_report_invalid')
    rows = report['candidates']
    full_verification = (report.get('status') == 'verified_study'
                         and report.get('reason') == 'all_candidates_and_receipt_match'
                         and report.get('receipt_validated') is True
                         and report.get('all_candidates_verified') is True and bool(rows)
                         and all(row.get('status') == 'verified_completion' for row in rows))
    status = report.get('status')
    if full_verification:
        status_label, tone = '연구 검증 완료', 'ok'
    elif status == 'blocked':
        status_label, tone = '연구 검사 차단', 'blocked'
    elif status == 'unavailable':
        status_label, tone = '연구 확인 불가', 'blocked'
    else:
        status_label, tone = '연구 검증 미완료', 'pending'
    receipt = '확인됨' if full_verification else '미검증'
    symbol = report.get('symbol')
    quote = symbol.split('/', 1)[1] if isinstance(symbol, str) and '/' in symbol else ''
    common = _mapping(report.get('common'))
    overview = ''.join((
        _field('symbol', '시장', symbol), _field('timeframe', '봉 간격', report.get('timeframe')),
        _field('bar_count', '원본 봉 수', report.get('bar_count'), '개'),
        _field('baseline', '기준 후보', report.get('baseline')),
    ))
    assumptions = ''.join(_field(key, title, common.get(key), quote if key == 'initial_balance' else '%')
                          for key, title in _COMMON)
    candidates = ''.join(_row(row, quote, report.get('baseline'), full_verification) for row in rows)
    if not rows:
        candidates = '<div class="panel empty">후보 정보를 불러올 수 없습니다. 아래 진단 코드를 확인하세요.</div>'
    delta_note = ('기준 대비 차이는 검사 결과의 퍼센트포인트(pp) 값을 그대로 표시합니다.' if full_verification else
                  '기준 대비 차이는 연구 전체·완료 기록·모든 후보가 검증된 경우에만 표시합니다.')
    provenance = _diagnostics((
        ('검사 상태 코드', status), ('진단 코드', report.get('reason')),
        ('원본 SHA-256', report.get('source_sha256')), ('검사 경계', report.get('boundaries')),
    ))
    return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<meta name="referrer" content="no-referrer"><title>시장 모의 연구 · 검사 보고서</title>'
            '<style>'+_CSS+'</style></head><body><main><header class="intro"><div>'
            '<p class="eyebrow">UULAB / MARKET STUDY</p><h1>시장 모의 연구</h1>'
            '<p class="subtitle">동일한 조건, 독립된 모의 계정의 과거 관측 결과</p></div>'
            '<span class="readonly">읽기 전용 스냅샷</span></header>'
            '<section class="panel overview" aria-label="연구 검사 개요"><div class="status-line">'
            '<span class="badge '+tone+'" data-field="study-status">'+status_label+'</span>'
            '<span class="receipt">완료 기록(receipt) · <strong data-field="receipt-status">'+receipt
            +'</strong></span></div><dl class="facts">'+overview+'</dl>'
            '<p class="observed">검사 관측 시각 · '+_e(report.get('observed_at'))+'</p></section>'
            '<section class="panel assumption-panel" aria-labelledby="assumptions-title">'
            '<h2 id="assumptions-title">공통 조건</h2><p class="section-note">계정별 독립 자본 · 비용은 시뮬레이션 가정입니다.</p>'
            '<dl class="assumptions">'+assumptions+'</dl></section>'
            '<section aria-labelledby="candidates-title"><div class="section-heading">'
            '<h2 id="candidates-title">후보별 관측 결과</h2><p>원본 계획 순서 · 독립 계정별 표시</p></div>'
            '<p class="delta-note">'+delta_note+'</p>'+candidates+'</section>'
            '<aside class="notice" aria-label="해석 안내"><p>이 보고서는 저장된 과거 가격을 사용한 역사적 시뮬레이션의 스냅샷입니다. '
            '각 계정은 순차 관측되며 동시 시점의 결과가 아닙니다.</p><p>이후 계정이나 파일이 바뀌면 다시 검사해야 합니다. '
            '개별 후보의 검증과 연구 전체의 완료 기록 검증은 구분됩니다.</p>'
            '<p>낙폭 제한은 중지 설정값입니다. 측정된 최대 낙폭(MDD)이나 승률을 뜻하지 않습니다. '
            '미래 성과, 표본 외 검증 또는 실거래 준비 상태를 보장하지 않습니다.</p></aside>'
            '<details class="provenance"><summary>검사 출처 및 기술 진단</summary>'+provenance+'</details>'
            '<p class="footer">PAPER · 저장된 검사 결과 · 자동 선택 및 실거래 권한 없음</p>'
            '</main></body></html>')
