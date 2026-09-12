import yaml

from app.config import load_watchlist


def _tickers_from(raw_yaml: str) -> list[str]:
    parsed = yaml.safe_load(raw_yaml)
    tickers = parsed.get("tickers", [])
    seen, out = set(), []
    for t in tickers:
        if isinstance(t, bool):
            continue
        symbol = str(t).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def test_unquoted_yaml_keyword_ticker_is_skipped_not_mangled():
    # "ON" unquoted is read by YAML as the boolean True, not the string "ON".
    # We must skip it rather than silently turning it into the symbol "TRUE".
    result = _tickers_from("tickers:\n  - AAPL\n  - ON\n  - MSFT\n")
    assert "TRUE" not in result
    assert result == ["AAPL", "MSFT"]


def test_quoted_yaml_keyword_ticker_is_kept():
    result = _tickers_from('tickers:\n  - AAPL\n  - "ON"\n  - MSFT\n')
    assert result == ["AAPL", "ON", "MSFT"]


def test_real_watchlist_file_has_no_boolean_entries():
    # Regression check on the shipped config/watchlist.yaml itself.
    tickers = load_watchlist()
    assert "TRUE" not in tickers
    assert "FALSE" not in tickers
    assert "ON" in tickers  # ON Semiconductor should load as a real symbol
