"""Reviewable Phase 7 candidate identities; certification output is authoritative."""

from __future__ import annotations

from dataclasses import dataclass

from .market import MarketInstrument, ProviderMarketInstrument, ValuationSupport


@dataclass(frozen=True, slots=True)
class CompanyCandidate:
    ticker: str
    name: str
    legal_name: str
    cik: str
    exchange_mic: str
    aliases: tuple[str, ...] = ()
    alternate_symbols: tuple[str, ...] = ()

    @property
    def instrument_id(self) -> str:
        return f"us-{self.exchange_mic.lower()}-{self.ticker.lower()}"

    def instruments(self) -> tuple[MarketInstrument, ...]:
        primary = MarketInstrument(
            self.instrument_id, self.ticker, self.ticker, self.exchange_mic, "USD"
        )
        alternates = tuple(
            MarketInstrument(
                f"us-{self.exchange_mic.lower()}-{symbol.lower()}", self.ticker, symbol,
                self.exchange_mic, "USD", share_class="C" if symbol == "GOOG" else None,
                is_primary=False, valuation_status=ValuationSupport.PARTIAL,
            )
            for symbol in self.alternate_symbols
        )
        return (primary, *alternates)


def _c(
    ticker: str, name: str, cik: int, exchange: str = "XNAS", *,
    legal_name: str | None = None, aliases: tuple[str, ...] = (),
    alternate_symbols: tuple[str, ...] = (),
) -> CompanyCandidate:
    return CompanyCandidate(
        ticker, name, legal_name or name, str(cik).zfill(10), exchange,
        aliases, alternate_symbols,
    )


# Exactly the 60 issuers named by Phase7-AGENT.md.  This manifest is input,
# never a whitelist: normal runtime queries expose only persisted PASS rows.
CANDIDATES: tuple[CompanyCandidate, ...] = (
    _c("AAPL", "Apple", 320193, aliases=("Apple Inc.",)),
    _c("MSFT", "Microsoft", 789019, aliases=("Microsoft Corporation",)),
    _c("NVDA", "NVIDIA", 1045810, aliases=("Nvidia", "NVIDIA Corporation")),
    _c("AVGO", "Broadcom", 1730168), _c("AMD", "Advanced Micro Devices", 2488),
    _c("QCOM", "Qualcomm", 804328), _c("TXN", "Texas Instruments", 97476),
    _c("ADI", "Analog Devices", 6281), _c("AMAT", "Applied Materials", 6951),
    _c("LRCX", "Lam Research", 707549), _c("KLAC", "KLA", 319201),
    _c("MU", "Micron Technology", 723125), _c("INTC", "Intel", 50863),
    _c("CSCO", "Cisco", 858877), _c("ORCL", "Oracle", 1341439, "XNYS"),
    _c("ADBE", "Adobe", 796343), _c("CRM", "Salesforce", 1108524, "XNYS"),
    _c("INTU", "Intuit", 896878),
    _c("GOOGL", "Alphabet", 1652044, aliases=("Google", "Alphabet Inc."), alternate_symbols=("GOOG",)),
    _c("META", "Meta Platforms", 1326801), _c("NFLX", "Netflix", 1065280),
    _c("CMCSA", "Comcast", 1166691), _c("DIS", "Walt Disney", 1744489, "XNYS", aliases=("Disney",)),
    _c("AMZN", "Amazon", 1018724), _c("TSLA", "Tesla", 1318605),
    _c("HD", "Home Depot", 354950, "XNYS"), _c("LOW", "Lowe's", 60667, "XNYS"),
    _c("MCD", "McDonald's", 63908, "XNYS"), _c("SBUX", "Starbucks", 829224),
    _c("NKE", "Nike", 320187, "XNYS"), _c("BKNG", "Booking Holdings", 1075531),
    _c("ORLY", "O'Reilly Automotive", 898173),
    _c("WMT", "Walmart", 104169, "XNYS"), _c("COST", "Costco", 909832),
    _c("PG", "Procter & Gamble", 80424, "XNYS"), _c("KO", "Coca-Cola", 21344, "XNYS"),
    _c("PEP", "PepsiCo", 77476), _c("MDLZ", "Mondelez International", 1103982),
    _c("CL", "Colgate-Palmolive", 21665, "XNYS"),
    _c("JNJ", "Johnson & Johnson", 200406, "XNYS"), _c("LLY", "Eli Lilly", 59478, "XNYS"),
    _c("MRK", "Merck", 310158, "XNYS"), _c("ABBV", "AbbVie", 1551152, "XNYS"),
    _c("AMGN", "Amgen", 318154), _c("GILD", "Gilead Sciences", 882095),
    _c("TMO", "Thermo Fisher Scientific", 97745, "XNYS"),
    _c("ABT", "Abbott Laboratories", 1800, "XNYS"),
    _c("ISRG", "Intuitive Surgical", 1035267),
    _c("CAT", "Caterpillar", 18230, "XNYS"), _c("DE", "Deere & Company", 315189, "XNYS"),
    _c("UPS", "United Parcel Service", 1090727, "XNYS"),
    _c("UNP", "Union Pacific", 100885, "XNYS"), _c("LMT", "Lockheed Martin", 936468, "XNYS"),
    _c("NOC", "Northrop Grumman", 1133421, "XNYS"),
    _c("WM", "Waste Management", 823768, "XNYS"),
    _c("ITW", "Illinois Tool Works", 49826, "XNYS"),
    _c("XOM", "Exxon Mobil", 2115436, "XNYS", legal_name="ExxonMobil Holdings Corp"),
    _c("CVX", "Chevron", 93410, "XNYS"), _c("COP", "ConocoPhillips", 1163165, "XNYS"),
    _c("EOG", "EOG Resources", 821189, "XNYS"),
)

CANDIDATE_BY_TICKER = {item.ticker: item for item in CANDIDATES}


def provider_instrument(instrument: MarketInstrument, provider: str = "TIINGO") -> ProviderMarketInstrument:
    return ProviderMarketInstrument(instrument.instrument_id, provider, instrument.symbol, instrument.exchange_mic)
