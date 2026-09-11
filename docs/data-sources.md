# Market data sources

Tiingo EOD is the active/default market-data provider. This project uses a local bring-your-own-key model: set `TIINGO_API_TOKEN` and invoke an explicit `reasonframe market ...` refresh. Application startup and normal Company, Compare, Search, analyst, and evidence operations read SQLite only.

One Tiingo historical EOD response is normalized into canonical raw and provider-adjusted OHLCV observations. `divCash != 0` creates a cash-dividend action on Tiingo's documented ex-date. `splitFactor != 1` creates a split action on that ex-date. Tiingo defines `splitFactor` as `splitTo / splitFrom`, matching the canonical `new_shares_per_old_share` convention. No separate corporate-action endpoint is used.

Raw close is used for quoted prices and valuation numerators. Provider-adjusted close is used for performance charts, deterministic returns, indexed comparisons, and drawdowns. Adjusted values remain provider-supplied and are described as “provider-adjusted.”

Local evidence records the provider, canonical instrument, source symbol, exchange, trading/event date, field or action semantics, value, and retrieval timestamp. Valuation evidence combines Tiingo raw-close evidence with SEC denominator and split-basis evidence plus the deterministic formula.

The catalog reports `market_provider=TIINGO`, `provider_connected`, and a market connection state without exposing any token. Missing credentials do not prevent SEC, FRED, or other local functionality. Authentication, rate-limit, unavailable-instrument, provider-availability, and malformed-response failures use provider-neutral error codes.

The old Marketstack adapter is retained only for temporary equivalence/regression audits. It is not imported by active refresh runtime, has no entry in `.env.example`, and is not a product-selectable provider.

EOD market data sourced from [Tiingo](https://www.tiingo.com/documentation/end-of-day).
