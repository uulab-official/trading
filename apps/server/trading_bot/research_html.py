"""Pure HTML projection of a validated saved walk-forward research report.

Consistency checks do not authenticate the data source or attest execution.
This module reads no files and imports no research or trading executor.
"""
from datetime import datetime, timedelta, timezone
from html import escape

from . import research_report


_CSS = """
:root{color-scheme:light;--ink:#182331;--sub:#536276;--line:#dce3eb;--paper:#fff;
--bg:#f4f6f9;--blue:#315fe8;--green:#12644e;--amber:#855510}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:13px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
main{width:min(1200px,100%);margin:auto;padding:32px 28px 40px}h1,h2,h3,h4,p,dl{margin:0}
h1{font-size:27px;line-height:1.35;letter-spacing:-1.1px}h2{font-size:16px;letter-spacing:-.35px}
h3{font-size:15px}h4{font-size:12px}.eyebrow{font-size:10px;font-weight:750;letter-spacing:1.7px;
color:var(--blue);margin-bottom:8px}.intro,.heading{display:flex;justify-content:space-between;
align-items:flex-start;gap:16px}.intro{margin-bottom:20px}.subtitle,.note{color:var(--sub);font-size:11px}
.subtitle{margin-top:8px}.readonly{background:#eaf0ff;color:#3153a5;border:1px solid #dbe5fc;
border-radius:6px;padding:4px 9px;white-space:nowrap;font-size:11px}.panel,.fold{background:var(--paper);
border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:15px}
.badge{display:inline-block;background:#e6f4ee;color:var(--green);font-size:11px;font-weight:700;
padding:3px 8px;border-radius:5px}.skip{background:#fff3d9;color:var(--amber)}
.facts,.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px 22px;margin-top:13px}
.facts>div,.metrics>div{min-width:0}dt{color:var(--sub);font-size:11px}dd{margin:3px 0 0;
overflow-wrap:anywhere;font-variant-numeric:tabular-nums;font-weight:550}.metrics dd{font-size:12px}
.boundaries{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 20px;margin:13px 0}
.boundaries dd{font-size:11px}.boundary-note{font-size:10px;color:var(--sub)}
section+section{margin-top:22px}.fold details{margin-top:13px}details{font-size:11px;color:var(--sub)}
summary{cursor:pointer;width:fit-content;max-width:100%;padding:3px 0;font-weight:600}
summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px;border-radius:2px}
.score,.scenario,.comparison{border-top:1px solid #edf0f4;padding-top:12px;margin-top:12px}
.score h4,.scenario h4{color:var(--ink);overflow-wrap:anywhere}.candidate-list{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.candidate{background:#f4f6fa;border-radius:6px;padding:7px 10px;overflow-wrap:anywhere;font-size:11px}
.candidate strong{color:var(--ink)}.notice{background:#eef2f8;border:1px solid var(--line);border-radius:8px;
padding:13px 16px;margin-top:17px;color:#4b5b70;font-size:11px}.notice p+p{margin-top:5px}
.provenance{margin-top:17px}.provenance dd{font-size:11px;font-weight:400}.footer{margin-top:17px;
font-size:10px;color:#6b7788}.unavailable{padding-top:10px;color:var(--sub)}
@media(max-width:700px){main{padding:22px 15px 30px}h1{font-size:23px}.intro{gap:10px}
.readonly{font-size:10px;padding:3px 6px}.panel,.fold{padding:15px}.facts,.metrics{grid-template-columns:
repeat(2,minmax(0,1fr));gap:12px}.boundaries{grid-template-columns:1fr;gap:7px}.heading{gap:8px}}
@media print{body{background:#fff}main{width:100%;padding:0}.panel,.score,.scenario{break-inside:avoid}
.notice{background:#fff}summary{color:var(--ink)}}
"""

_METRICS = (
    ('equity', '평가자산'), ('return_pct', '수익률'), ('cash', '현금'),
    ('position_value', '보유 포지션 평가액'), ('fees', '누적 수수료'), ('fills', '체결 수'),
    ('daily_loss_halted', '일일 손실 중지'), ('drawdown_halted', '낙폭 중지'),
)
_SUMMARY = (
    ('total_folds', '전체 창'), ('evaluated_folds', '평가 창'), ('skipped_folds', '건너뛴 창'),
    ('positive_folds', '양수 수익 창'), ('mean_test_return_pct', '평균 테스트 수익률'),
    ('worst_test_return_pct', '최저 테스트 수익률'), ('best_test_return_pct', '최고 테스트 수익률'),
    ('unused_tail_bars', '미사용 마지막 봉'),
)
_ROBUST_SUMMARY = (
    ('multiplier', '비용 배수'), ('total_folds', '전체 창'),
    ('mean_strategy_return_pct', '전략 평균 수익률'), ('mean_buy_hold_return_pct', '매수·보유 평균 수익률'),
    ('mean_excess_return_pp', '평균 초과 수익률'), ('worst_strategy_return_pct', '전략 최저 수익률'),
    ('positive_folds', '전략 양수 수익 창'), ('outperformed_benchmark_folds', '매수·보유 초과 창'),
)


def _e(value):
    return escape(str(value), quote=True)


def _field(key, label, value, unit=''):
    return ('<div><dt>'+_e(label)+'</dt><dd data-field="'+_e(key)+'">'+_e(value)
            +(' '+_e(unit) if unit else '')+'</dd></div>')


def _utc(milliseconds):
    # Integer timedelta arithmetic avoids local timezone and float rounding,
    # including the last representable millisecond of year 9999.
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)
    return instant.isoformat(timespec='milliseconds').replace('+00:00', ' UTC')


def _metrics(values, quote, role):
    fields = []
    for key, label in _METRICS:
        value = values[key]
        if type(value) is bool:
            value, unit = ('예' if value else '아니요'), ''
        else:
            unit = '%' if key == 'return_pct' else ('건' if key == 'fills' else quote)
        fields.append(_field(key, label, value, unit))
    return '<dl class="metrics" data-role="'+_e(role)+'">'+''.join(fields)+'</dl>'


def _candidate(candidate):
    parameters = [('빠른 기간', 'fast_period'), ('느린 기간', 'slow_period')] if candidate[
        'strategy'] == 'ma_cross' else [('RSI 기간', 'rsi_period')]
    return ('<div class="candidate" data-role="candidate" data-candidate-id="'+_e(candidate['id'])
            +'"><strong>'+_e(candidate['id'])+'</strong> · '+_e(candidate['strategy'])
            +''.join(' · '+_e(label)+' '+_e(candidate[key]) for label, key in parameters)+'</div>')


def _boundaries(fold, spec, duration):
    fields = []
    for group, label in (('train', '학습'), ('test', '테스트')):
        start, end = fold[group+'_start'], fold[group+'_end']
        fields.extend((
            _field(group+'-range', label+' 봉 인덱스', f'[{start}, {end})'),
            _field(group+'-first-utc', label+' 첫 포함 봉 · UTC',
                   _utc(spec['first_bar_ms']+start*duration)),
            _field(group+'-last-utc', label+' 마지막 포함 봉 · UTC',
                   _utc(spec['first_bar_ms']+(end-1)*duration)),
        ))
    return ('<dl class="boundaries">'+''.join(fields)+'</dl>'
            '<p class="boundary-note">[시작, 끝) 인덱스 · 끝 봉은 제외하며 시각은 실제 포함 봉의 시작 시각입니다.</p>')


def _cost_diagnostics(diagnostic, quote, skipped):
    fields = ''.join((
        _field('entry_open', '참조 진입 시가', diagnostic['entry_open'], quote),
        _field('terminal_close', '참조 마지막 종가', diagnostic['terminal_close'], quote),
        _field('entry_bar_ms', '참조 진입 봉 · UTC', _utc(diagnostic['entry_bar_ms'])),
        _field('mark_bar_ms', '참조 평가 봉 · UTC', _utc(diagnostic['mark_bar_ms'])),
    ))
    scenarios = []
    for row in diagnostic['scenarios']:
        costs = (_field('fee_pct', '수수료율', row['fee_pct'], '%')
                 +_field('slippage_pct', '슬리피지율', row['slippage_pct'], '%')
                 +_field('excess_return_pp', '매수·보유 대비 초과 수익률', row['excess_return_pp'], 'pp'))
        scenarios.append('<section class="scenario" data-role="cost-scenario" data-multiplier="'
                         +_e(row['multiplier'])+'"><h4>'+_e(row['multiplier'])+'x 비용</h4>'
                         '<dl class="facts">'+costs+'</dl><h4>'
                         +('전략 · 현금 유지 진단' if skipped else '고정 선택 전략')+'</h4>'
                         +_metrics(row['strategy'], quote, 'strategy')+'<h4>매수·보유 참조</h4>'
                         +_metrics(row['buy_hold'], quote, 'buy_hold')+'</section>')
    return ('<details><summary>비용별 전략·매수·보유 진단'+(' · 현금 유지 진단' if skipped else '')
            +'</summary><dl class="facts">'+fields+'</dl>'+''.join(scenarios)+'</details>')


def _fold(fold, index, spec, duration, diagnostic):
    skipped = fold['selected'] is None
    content = ('<article class="fold" data-fold="'+_e(index)+'"><div class="heading"><h3>창 '
               +_e(index)+'</h3><span class="badge'+(' skip' if skipped else '')+'">'
               +('건너뜀' if skipped else '저장된 테스트 결과')+'</span></div>'
               +_boundaries(fold, spec, duration)+'<dl class="facts">'
               +_field('selected', '학습에서 선택된 후보', '선택 없음' if skipped else fold['selected'])+'</dl>')
    if skipped:
        content += ('<p class="unavailable">측정된 테스트 결과 없음 · 최소 학습 체결 수를 충족한 후보가 없어 건너뜀. '
                    '전체 창 요약에는 현금 유지의 수익률 0으로 기여합니다.</p>')
    else:
        content += _metrics(fold['test'], spec['symbol'].split('/')[1], 'test-metrics')
    content += '<details><summary>학습 후보 전체 점수 · 저장된 순서</summary>'
    for score in fold['train_scores']:
        content += ('<section class="score" data-role="training-score" data-candidate-id="'
                    +_e(score['candidate_id'])+'"><h4>'+_e(score['candidate_id'])+'</h4>'
                    +_metrics(score['metrics'], spec['symbol'].split('/')[1], 'training-metrics')+'</section>')
    content += '</details>'
    if diagnostic is not None:
        content += _cost_diagnostics(diagnostic, spec['symbol'].split('/')[1], skipped)
    return content+'</article>'


def _robustness(robustness):
    if robustness is None:
        return '<section class="panel"><h2>비용 강건성 미실시</h2><p class="note">스키마 1에는 비용별 전략·참조 진단이 없습니다.</p></section>'
    rows = []
    for row in robustness['summary']:
        fields = ''.join(_field(key, label, row[key],
                               'pp' if key.endswith('_pp') else ('%' if key.endswith('_pct') else
                               ('x' if key == 'multiplier' else '개')))
                         for key, label in _ROBUST_SUMMARY)
        rows.append('<section class="comparison" data-role="robustness-summary" data-multiplier="'
                    +_e(row['multiplier'])+'"><h3>'+_e(row['multiplier'])+'x 비용 요약</h3>'
                    '<dl class="facts">'+fields+'</dl></section>')
    return ('<section class="panel"><h2>고정 선택 비용 강건성</h2>'
            '<p class="note">기본 비용의 학습 선택을 고정하여 1x·2x·3x에서 재사용합니다. '
            '모든 창을 포함한 산술 평균이며 초과 수익률은 퍼센트포인트(pp)입니다.</p>'
            '<p class="note">매수·보유 참조: 초기 자본의 설정 포지션 비중으로 두 번째 테스트 봉 시가에 매수, '
            '마지막 테스트 봉 종가로 평가합니다. 수량은 소수점 8자리 아래를 버립니다. '
            '동일 비용을 적용하고 능동적 위험 중지는 적용하지 않으며, 강제 청산 없이 현금 이자는 0입니다.</p>'
            +''.join(rows)+'</section>')


def render_research_report(report: dict) -> str:
    """Validate saved-report consistency and project exact values without I/O."""
    report = research_report.validate_report(report)
    spec, summary = report['experiment'], report['summary']
    settings, quote = spec['settings'], spec['symbol'].split('/')[1]
    frame = spec['timeframe']
    duration = int(frame[:-1])*{'m':60000, 'h':3600000, 'd':86400000, 'w':604800000}[frame[-1]]
    robustness = report.get('robustness')
    overview = ''.join((
        _field('symbol', '시장 / 평가 통화', spec['symbol']),
        _field('timeframe', '봉 간격', frame), _field('bars', '전체 봉 수', spec['bars'], '개'),
        _field('schema_version', '스키마 버전', report['schema_version']),
        _field('first-bar-utc', '원본 첫 포함 봉 · UTC', _utc(spec['first_bar_ms'])),
        _field('last-bar-utc', '원본 마지막 포함 봉 · UTC',
               _utc(spec['first_bar_ms']+(spec['bars']-1)*duration)),
    ))
    assumptions = []
    for key, label in (('initial_balance', '창별 초기 자본'), ('position_size_pct', '포지션 비중'),
                       ('fee_pct', '기본 수수료율'), ('slippage_pct', '기본 슬리피지율'),
                       ('max_daily_loss_pct', '일일 손실 중지 설정'), ('max_drawdown_pct', '낙폭 중지 설정')):
        assumptions.append(_field(key, label, settings[key], quote if key == 'initial_balance' else '%'))
    for key, label in (('train_size', '학습 봉 수'), ('test_size', '테스트 봉 수'), ('gap', '학습·테스트 간격'),
                       ('warmup', '최대 사전 준비 봉 수'), ('min_training_fills', '최소 학습 체결 수')):
        assumptions.append(_field(key, label, settings[key], '건' if key == 'min_training_fills' else '개'))
    assumptions.append(_field('anchored', '학습 창 정책', '앵커 확장 (anchored)' if settings['anchored'] else '롤링 (rolling)'))
    original_summary = ''.join(_field(key, label, summary[key], '%' if key.endswith('_pct') else '개')
                               for key, label in _SUMMARY)
    folds = ''.join(_fold(fold, index, spec, duration, robustness['folds'][index-1] if robustness else None)
                    for index, fold in enumerate(report['folds'], 1))
    provenance = ''.join((
        _field('generated_at', '저장된 보고서 생성 시각', report['generated_at']),
        _field('run_id', '실험 설정 식별자', report['run_id']),
        _field('fixture_sha256', '보고된 입력 fixture SHA-256', spec['fixture_sha256']),
    ))
    return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<meta name="referrer" content="no-referrer"><title>워크포워드 연구 · 저장 보고서</title>'
            '<style>'+_CSS+'</style></head><body><main><header class="intro"><div>'
            '<p class="eyebrow">UULAB / WALK-FORWARD RESEARCH</p><h1>워크포워드 연구</h1>'
            '<p class="subtitle">저장된 과거 모의 연구 · 학습 선택과 테스트 관측의 전체 기록</p></div>'
            '<span class="readonly">읽기 전용 스냅샷</span></header><section class="panel">'
            '<span class="badge" data-field="research-status">연구 보고서 일관성 확인</span>'
            '<p class="subtitle">저장된 구조·식별자·산술의 일관성을 확인했습니다. 데이터 출처와 실제 실행은 '
            '인증하지 않으며, 시장 연구 완료 기록(receipt) 검증을 제공하지 않습니다.</p>'
            '<dl class="facts">'+overview+'</dl></section><section class="panel"><h2>공통 조건과 후보</h2>'
            '<p class="note">각 창은 독립 자본으로 시작합니다. 아래 후보와 선택은 저장된 보고서 값입니다.</p>'
            '<dl class="facts">'+''.join(assumptions)+'</dl><div class="candidate-list">'
            +''.join(_candidate(c) for c in spec['candidates'])+'</div></section>'
            '<section class="panel"><h2>원본 전체 창 요약</h2><p class="note">건너뛴 창을 포함합니다. '
            '창별 수익률의 산술 평균으로, 복리 누적 수익률이 아닙니다.</p><dl class="facts">'
            +original_summary+'</dl></section>'+_robustness(robustness)+'<section><h2>학습·테스트 창 전체</h2>'
            '<p class="note">손실과 건너뛴 창을 모두 보존합니다. 선택 후보를 다시 순위 매기지 않습니다.</p>'
            +folds+'</section><aside class="notice"><p>각 테스트 창은 독립 자본으로 계산됩니다. '
            '미청산 보유분은 마지막 포함 봉 종가의 평가자산에 반영하며 강제 청산하지 않습니다.</p>'
            '<p>일일 손실·낙폭 중지 표시는 설정에 따른 중지 발생 플래그입니다. 낙폭 제한 설정이나 중지 여부는 '
            '측정된 최대 낙폭(MDD)이 아닙니다. 미래 성과나 실거래 준비 상태를 보장하지 않습니다.</p>'
            '<p>실험 식별자는 저장된 설정의 일관성을 위한 해시입니다. 보고된 fixture 해시는 원본 가격을 다시 '
            '열어 확인하지 않았으며 데이터 출처나 실행의 증명이 아닙니다. 생성 시각은 원본 보고서의 값입니다.</p>'
            '</aside><details class="provenance"><summary>보고서 생성 시각 및 식별 정보</summary>'
            '<dl class="facts">'+provenance+'</dl></details><p class="footer">PAPER · 읽기 전용 · '
            '저장된 보고서 일관성 확인 · 실거래 권한 없음</p></main></body></html>')
