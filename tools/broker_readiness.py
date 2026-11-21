"""Report allowlisted broker configuration presence without authenticating.

Standard library only, including under ``python -S``. No broker transport,
dotenv, keychain, file access, token issuance, or network operation is used.
"""

import json
import os
import sys

_VARIABLES = (
    "TOSS_ACCESS_TOKEN",
    "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET",
    "KIS_VTS_ACCESS_TOKEN",
    "KIS_VTS_APPKEY",
    "KIS_VTS_APPSECRET",
    "KIS_VTS_CANO",
    "KIS_VTS_ACNT_PRDT_CD",
)
_CAPABILITIES = (
    (
        "toss_market_data",
        ("TOSS_ACCESS_TOKEN",),
        "Toss 기존 토큰으로 시세 조회",
        "docs/STOCK_PAPER_TOSS_KIS.md",
    ),
    (
        "toss_token_issuance",
        ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"),
        "Toss 명시적 토큰 발급 설정",
        "docs/STOCK_PAPER_TOSS_KIS.md",
    ),
    (
        "kis_vts_quote",
        ("KIS_VTS_ACCESS_TOKEN", "KIS_VTS_APPKEY", "KIS_VTS_APPSECRET"),
        "KIS VTS 현재가 조회",
        "docs/KIS_VTS_READONLY_QUOTE.md",
    ),
    (
        "kis_vts_order_inquiry",
        (
            "KIS_VTS_ACCESS_TOKEN",
            "KIS_VTS_APPKEY",
            "KIS_VTS_APPSECRET",
            "KIS_VTS_CANO",
            "KIS_VTS_ACNT_PRDT_CD",
        ),
        "KIS VTS 일별 주문내역 조회",
        "docs/KIS_VTS_ORDER_SNAPSHOT.md",
    ),
)
_FAILURE = "Broker configuration diagnosis failed; no broker request was made."
_HELP = """usage: broker_readiness.py [--format {json,text}]

Inspect only allowlisted environment-variable presence, without authenticating.
JSON is the default; text explains the result in Korean. Missing configuration
is a completed diagnosis (exit 0). Invalid invocation or diagnosis failure is
exit 2. No credential values, files, token issuance, or broker requests.

options:
  --format {json,text}  Output format (default: json)
  -h, --help            Show this help
"""


def inspect_environment(environ=None):
    """Return schema version 1 presence booleans, never credential values.

    ``None`` selects ``os.environ``; a supplied mapping is used even if empty.
    Only nonblank strings are present. This deliberately does not validate
    credential syntax, freshness, authenticity, permissions, or account binding.
    Failed lookups refuse the entire diagnosis with a fixed, unchained error.
    """
    try:
        environment = os.environ if environ is None else environ
        presence = {}
        for name in _VARIABLES:
            value = environment.get(name)
            # Use the built-in method so str subclasses cannot override strip.
            presence[name] = isinstance(value, str) and bool(str.strip(value))
    except Exception:
        raise ValueError(_FAILURE) from None

    capabilities = []
    for capability_id, required, _title, _guide in _CAPABILITIES:
        missing = [name for name in required if not presence[name]]
        capability = {
            "id": capability_id,
            "required_variables": list(required),
            "missing_variables": missing,
            "variable_presence": {name: presence[name] for name in required},
            "configuration_present": not missing,
            "authenticated": False,
        }
        if capability_id == "toss_token_issuance":
            capability["requires_explicit_opt_in"] = True
        capabilities.append(capability)

    return {
        "schema_version": 1,
        "mode": "paper",
        "live_orders_enabled": False,
        "safe_to_submit": False,
        "actual_broker_requests": 0,
        "capabilities": capabilities,
    }


def render_text(report):
    """Explain a presence report in Korean, using fixed names and boolean data.

    Display labels and missing-variable names come from the local allowlist,
    not arbitrary strings in a caller-provided report.
    """
    try:
        lines = [
            "브로커 설정 진단: 환경변수 존재 여부만 확인했습니다.",
            "mode=paper | live_orders_enabled=false | safe_to_submit=false | "
            "actual_broker_requests=0",
            "설정 존재는 인증 성공, 권한, 계좌 연결 또는 주문 가능을 뜻하지 않습니다.",
            "모든 기능은 미인증 상태입니다 (authenticated=false).",
            "",
        ]
        for capability_id, required, title, guide in _CAPABILITIES:
            item = next(item for item in report["capabilities"] if item["id"] == capability_id)
            presence = item["variable_presence"]
            missing = [name for name in required if presence.get(name) is not True]
            status = "설정 누락" if missing else "설정 존재 / 인증 미확인"
            lines.append(f"[{capability_id}] {title}: {status}")
            lines.append(
                "  변수: "
                + ", ".join(
                    name + ("=있음" if presence.get(name) is True else "=없음") for name in required
                )
            )
            lines.append("  누락: " + (", ".join(missing) if missing else "없음"))
            lines.append("  안내: " + guide)
        lines.extend(
            [
                "",
                "다음 단계: 누락된 값은 신뢰하는 로컬 세션에서 설정한 뒤 다시 진단하세요.",
                "실제 값은 출력하거나 공유하지 말고, 위 문서에서 별도 조회 절차를 확인하세요.",
                "Toss 토큰 발급은 기존 수집기의 --issue-token 명시적 선택이 필요합니다.",
                "이 진단은 토큰 발급·갱신, 인증, 네트워크 요청 또는 주문을 수행하지 않습니다.",
                "비어 있지 않은 문자열이면 존재로 표시하므로 만료·잘못된 형식도 구분하지 않습니다.",
            ]
        )
        return "\n".join(lines) + "\n"
    except Exception:
        raise ValueError(_FAILURE) from None


def main(argv=None):
    """Print one diagnosis; return 0 for completion and 2 for refusal/failure."""
    try:
        # A fixed parser avoids argument echoing and locale/file discovery.
        arguments = list(sys.argv[1:] if argv is None else argv)
        if arguments in (["--help"], ["-h"]):
            print(_HELP, end="")
            return 0
        if not arguments or arguments in (["--format", "json"], ["--format=json"]):
            output_format = "json"
        elif arguments in (["--format", "text"], ["--format=text"]):
            output_format = "text"
        else:
            raise ValueError(_FAILURE)
        report = inspect_environment()
        if output_format == "text":
            print(render_text(report), end="")
        else:
            print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    except Exception:
        # Do not expose exception text, argv, paths, environment names or values.
        print(_FAILURE, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
