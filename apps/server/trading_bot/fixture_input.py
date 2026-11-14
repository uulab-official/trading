"""Shared offline fixture decoder; no acquisition or execution is performed."""

def load_fixture(raw):
    """Preserve legacy float semantics; strictly revalidate annotated market files."""
    from trading_data.upbit_history import decode_json, validate_market_fixture
    data = decode_json(raw, decimal_numbers=False)
    if not isinstance(data, dict) or data.get('mode', 'paper') != 'paper':
        raise ValueError('paper fixture required')
    candles = data.get('candles')
    if (not isinstance(candles, list) or not 1 <= len(candles) <= 10000
            or any(not isinstance(row, list) or len(row) != 6 for row in candles)):
        raise ValueError('one to 10000 six-column OHLCV rows required')
    if 'data_provenance' in data:
        validate_market_fixture(data)
    return data
