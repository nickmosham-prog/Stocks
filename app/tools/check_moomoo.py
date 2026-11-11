"""One-time check that moomoo data works on this machine.

    python -m app.tools.check_moomoo AAPL

Prints whether OpenD is reachable, the expirations it lists, one raw quote
row, the option idea the screener would pick from moomoo's data (next to
Yahoo's for comparison), and moomoo's live stock prices for the watchlist.
Safe to run any time - it only reads data.
"""

from __future__ import annotations

import sys
from datetime import date

from app.config import load_settings, load_watchlist
from app.datasource.moomoo_source import MoomooOptionsClient, _moomoo_code
from app.datasource.yfinance_source import YFinanceSource
from app.scan import options_strategy


def _describe(contract: dict | None, reason: str | None) -> str:
    if not contract:
        return f"no contract ({reason})"
    theta = contract.get("theta")
    theta_text = f"theta {theta:.3f}/day" if theta is not None else "theta n/a"
    return (
        f"{contract['option_type'].upper()} ${contract['strike']:g} exp {contract['expiration']} "
        f"({contract['days_to_expiration']}d) @ ${contract['price']:.2f}, delta {contract['delta']:.2f}, "
        f"{theta_text}, IV {contract.get('implied_volatility') or 0:.0%}, OI {contract.get('open_interest') or 0:.0f}"
    )


def _money(value) -> str:
    return f"${value:,.2f}" if value else "n/a"


def check_live_prices(client: MoomooOptionsClient, yahoo, symbol: str) -> None:
    symbols = load_watchlist()
    if symbol not in symbols:
        symbols = [symbol] + symbols
    print(f"5) Live stock prices from moomoo ({len(symbols)} symbols):")
    quotes = client.get_stock_snapshots(symbols)
    if not quotes:
        print("   none returned - your account may not have live US stock quotes in OpenAPI.")
        return
    priced = [s for s in symbols if (quotes.get(s) or {}).get("price")]
    missing = [s for s in symbols if s not in priced]
    print(f"   {len(priced)} of {len(symbols)} quoted" + (f"; no quote for: {', '.join(missing[:20])}" if missing else ""))
    quote = quotes.get(symbol) or {}
    try:
        bars = yahoo.get_bulk_intraday_bars([symbol], period="1d", interval="5m", prepost=True).get(symbol)
        yahoo_price = float(bars["Close"].dropna().iloc[-1]) if bars is not None and not bars.empty else None
    except Exception:
        yahoo_price = None
    print(
        f"   {symbol}: moomoo last {_money(quote.get('price'))} (day high {_money(quote.get('high'))}, "
        f"low {_money(quote.get('low'))}, pre-market {_money(quote.get('pre_price'))}) "
        f"vs Yahoo's latest bar {_money(yahoo_price)}"
    )


def main(symbol: str) -> int:
    symbol = symbol.upper()
    settings = load_settings()
    cfg = settings.get("options_source", "moomoo", default={}) or {}
    client = MoomooOptionsClient(host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 11111)))
    print(f"1) Connecting to moomoo OpenD at {cfg.get('host', '127.0.0.1')}:{cfg.get('port', 11111)} ...")
    ok, message = client.check_connection()
    print(f"   {'OK' if ok else 'FAILED'}: {message}")
    if not ok:
        print("   -> Open the moomoo OpenD app, log in, and run this again.")
        return 1

    print(f"2) Option expirations for {symbol}:")
    expirations = client.get_option_expirations(symbol)
    print(f"   {expirations[:8] if expirations else 'none returned'}{' ...' if expirations and len(expirations) > 8 else ''}")
    if not expirations:
        print("   -> Your account may not have US options quote rights in OpenAPI (check moomoo's OpenAPI quote rights page).")
        check_live_prices(client, YFinanceSource(), symbol)
        client.close()
        return 1

    print("3) One raw quote row from moomoo (for checking field names/units):")
    try:
        ret, chain = client._invoke("get_option_chain", _moomoo_code(symbol))
    except Exception as exc:
        ret, chain = -1, exc
    if ret == 0 and len(chain):
        try:
            ret, snap = client._invoke("get_market_snapshot", [chain["code"].iloc[len(chain) // 2]])
        except Exception as exc:
            ret, snap = -1, exc
        if ret == 0 and len(snap):
            row = snap.iloc[0]
            for col in ("code", "last_price", "bid_price", "ask_price", "volume", "option_type", "strike_time",
                        "option_strike_price", "option_open_interest", "option_implied_volatility",
                        "option_delta", "option_theta"):
                print(f"   {col:<28} {row.get(col, '<missing>')}")
        else:
            print(f"   snapshot failed: {snap}")
    else:
        print(f"   option chain failed: {chain}")

    yahoo = YFinanceSource()
    spot = None
    bars = yahoo.get_daily_history(symbol, days=7)
    if bars is not None and not bars.empty:
        spot = float(bars["Close"].dropna().iloc[-1])
    if spot is None:
        print("4) (couldn't get a price from Yahoo to compare option ideas)")
    else:
        print(f"4) Option idea the screener would pick (bullish, {symbol} at ${spot:.2f}):")

        class _MoomooOnly:
            get_option_expirations = staticmethod(client.get_option_expirations)
            get_option_chain = staticmethod(lambda s, expirations=None: client.get_option_chain(s, expirations))

        for label, source in (("moomoo", _MoomooOnly()), ("yahoo ", yahoo)):
            contract, reason = options_strategy.find_recommended_contract_with_reason(
                source, symbol, spot, "bullish", settings
            )
            print(f"   {label}: {_describe(contract, reason)}")

    check_live_prices(client, yahoo, symbol)
    client.close()
    print(
        f"\nDone ({date.today()}). A moomoo contract in step 4 means options data works; "
        "prices in step 5 mean live prices work."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))
