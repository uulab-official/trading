"""Self-contained text-only rendering of an already-computed private overview."""
from html import escape
import json

LABELS = {
    'verified_completion': ('완료 기록 일치', 'ok'),
    'compatible_snapshot': ('미완료 · 기록 일치', 'info'),
    'reconciliation_required': ('체결 응답 확인 필요', 'warn'),
    'blocked': ('진단상 차단', 'bad'),
    'unavailable': ('확인 불가', 'bad'),
}
REASONS = {
    'artifacts_and_account_match': '보고서·체결·현재 계좌의 저장 기록이 일치합니다.',
    'prefix_matches': '저장 체결 순서가 원본 전략과 일치합니다. 아직 완료 결과는 아닙니다.',
    'saved_fill_confirmation_required': '저장된 모의 체결의 주문 응답 확인이 남아 있습니다.',
    'manifest_missing': '복구용 작업 정보가 없습니다. 오래된 작업을 자동 변환하지 않습니다.',
    'source_unavailable': '작업 파일을 읽을 수 없습니다. 경로와 접근 권한을 확인하세요.',
    'private_directory_required': '개인 소유의 비공개 일반 디렉터리가 필요합니다.',
    'source_changed': '검사 중 작업 경로나 완료 기록이 변경됐습니다.',
    'replay_mismatch': '완료 보고서가 원본 전략의 재현 결과와 다릅니다.',
    'account_changed_after_completion': '현재 계좌가 종료 시점의 저장 기록과 다릅니다.',
    'completion_present': '검사 중 완료 파일이 확인됐습니다. 새로 점검해야 합니다.',
}


def _text(value):
    return escape(str(value), quote=True)


def render_overview(report):
    """No scripts, network assets, executable actions or portfolio arithmetic."""
    cards = []
    for row in report['runs']:
        title, style = LABELS.get(row['status'], ('확인 불가', 'bad'))
        diagnostic = row['diagnostic']
        counts = diagnostic.get('counts', {})
        saved = counts.get('fills', counts.get('saved_fills', '—'))
        remaining = counts.get('remaining_fills', '—')
        kind = '완료 결과 대조' if row['inspection'] == 'completion_audit' else '복구 사전 진단'
        if row['inspection'] == 'undetermined':
            kind = '원본 확인 실패'
        details = _text(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True, indent=2))
        explanation = REASONS.get(row['reason'], '상세 진단의 사유 코드를 확인하세요.')
        cards.append(f'''<article><div class="row"><h2>{_text(row['label'])}</h2>
<span class="badge {style}">{title}</span></div><p class="muted">{kind}</p>
<p>{_text(explanation)}</p><dl><div><dt>저장 체결</dt><dd>{_text(saved)}</dd></div>
<div><dt>남은 예상 체결</dt><dd>{_text(remaining)}</dd></div></dl>
<p class="stamp">개별 검사 완료: {_text(row['finished_at'])}</p>
<details><summary>진단 근거 · {_text(row['reason'])}</summary><pre>{details}</pre></details></article>''')
    counts = report['counts']
    good = counts['verified_completion']
    pending = counts['compatible_snapshot']
    attention = counts['reconciliation_required'] + counts['blocked'] + counts['unavailable']
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>모의 운용 점검</title>
<style>
:root{{color-scheme:light dark;--bg:#f3f6fa;--card:#fff;--fg:#1d2939;--muted:#66768a;--line:#dce4ef}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif}}
main{{max-width:1080px;margin:auto;padding:36px 24px 56px}}header{{margin-bottom:24px}}.eyebrow{{font-size:12px;letter-spacing:.12em;color:var(--muted)}}
h1{{font-size:30px;line-height:1.3;margin:8px 0 12px}}h2{{font-size:18px;margin:0;overflow-wrap:anywhere}}
p{{margin:8px 0}}.muted,.stamp,dt{{color:var(--muted)}}.stamp{{font-size:12px;overflow-wrap:anywhere}}
.notice{{padding:16px 20px;background:var(--card);border-left:4px solid #c78a1e;border-radius:8px;margin:20px 0}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:24px 0}}.stat,article{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}}
.stat b{{display:block;font-size:30px;margin-top:4px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}.row{{display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap}}
.badge{{font-size:12px;border-radius:30px;padding:4px 10px}}.ok{{background:#e7f5ed;color:#226641}}.info{{background:#e9f1ff;color:#28588f}}.warn{{background:#fff2d7;color:#815516}}.bad{{background:#fcebec;color:#9c3848}}
dl{{display:flex;gap:32px;margin:16px 0}}dt{{font-size:12px}}dd{{margin:0;font-size:21px;font-weight:650}}details{{border-top:1px solid var(--line);padding-top:12px;margin-top:14px}}summary{{cursor:pointer;font-size:13px;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.5}}footer{{margin-top:24px;font-size:12px;color:var(--muted)}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111823;--card:#1a2534;--fg:#e4ecf7;--muted:#a0b0c5;--line:#344155}}}}
@media(max-width:650px){{main{{padding:24px 16px}}h1{{font-size:26px}}.grid{{grid-template-columns:1fr}}.stats{{gap:8px}}.stat{{padding:12px;font-size:12px}}.stat b{{font-size:26px}}}}
</style></head><body><main><header><div class="eyebrow">UULAB · PAPER OPERATIONS</div>
<h1>모의 운용 점검</h1><p class="muted">작업별 완료·미완료·확인 필요 상태를 한 번에 살펴봅니다.</p>
<p class="stamp">전체 점검 종료: {_text(report['finished_at'])} · {_text(counts['total'])}개 작업</p></header>
<div class="notice"><strong>저장 기록을 검사한 정적 보고서입니다.</strong><p>작업별 조회 시각이 다릅니다. 현재 실행 여부나 실시간 시세를 보여주지 않으며, 실거래·복구 실행을 승인하지 않습니다. 계좌는 변경하지 않았습니다.</p></div>
<section class="stats" aria-label="상태 요약"><div class="stat">완료 기록 일치<b>{good}</b></div>
<div class="stat">미완료 · 기록 일치<b>{pending}</b></div><div class="stat">확인 필요 · 차단<b>{attention}</b></div></section>
<section class="grid" aria-label="작업별 진단">{''.join(cards)}</section>
<footer>잔고·통화·수익률은 작업 간 합산하지 않습니다. 처음 점검한 작업은 이후 변경됐을 수 있습니다.
진단 상세에는 개인 계좌 식별자가 포함될 수 있으므로 이 파일을 비공개로 보관하세요.</footer></main></body></html>'''
