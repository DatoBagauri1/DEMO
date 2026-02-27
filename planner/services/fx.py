import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.utils import timezone

from planner.models import FxRate
from planner.services.http_client import build_http_client

logger = logging.getLogger(__name__)

ONE_CENT = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(ONE_CENT, rounding=ROUND_HALF_UP)


def to_minor_units(value: Decimal | float | str) -> int:
    decimal_value = quantize_money(Decimal(str(value)))
    return int(decimal_value * 100)


def from_minor_units(value: int) -> Decimal:
    return Decimal(value) / Decimal("100")


@dataclass
class FxRateQuote:
    base_currency: str
    quote_currency: str
    rate: Decimal
    as_of: datetime
    source: str


class FxProvider(ABC):
    name = "base_fx"

    @abstractmethod
    def fetch_rates(self, base_currency: str, quote_currencies: Iterable[str]) -> list[FxRateQuote]:
        raise NotImplementedError


class FreeCurrencyApiProvider(FxProvider):
    name = "freecurrencyapi"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "https://api.freecurrencyapi.com/v1/latest").rstrip("/")

    def fetch_rates(self, base_currency: str, quote_currencies: Iterable[str]) -> list[FxRateQuote]:
        symbols = ",".join(sorted(set(code.upper() for code in quote_currencies if code and code.upper() != base_currency.upper())))
        if not symbols:
            return []

        with build_http_client(accept="application/json") as client:
            response = client.get(
                self.base_url,
                params={
                    "apikey": self.api_key,
                    "base_currency": base_currency.upper(),
                    "currencies": symbols,
                },
            )
            response.raise_for_status()
            payload = response.json()
        rates = payload.get("data", {})
        now = timezone.now()
        quotes: list[FxRateQuote] = []
        for quote_currency, rate in rates.items():
            quotes.append(
                FxRateQuote(
                    base_currency=base_currency.upper(),
                    quote_currency=quote_currency.upper(),
                    rate=Decimal(str(rate)),
                    as_of=now,
                    source=self.name,
                ),
            )
        return quotes


class FallbackFxProvider(FxProvider):
    name = "fallback"

    def fetch_rates(self, base_currency: str, quote_currencies: Iterable[str]) -> list[FxRateQuote]:
        now = timezone.now()
        quotes: list[FxRateQuote] = []
        for quote in sorted(set(code.upper() for code in quote_currencies if code)):
            quotes.append(
                FxRateQuote(
                    base_currency=base_currency.upper(),
                    quote_currency=quote.upper(),
                    rate=Decimal("1.0"),
                    as_of=now,
                    source=self.name,
                ),
            )
        return quotes


def get_fx_provider() -> FxProvider:
    api_key = os.getenv("FX_API_KEY", "").strip()
    if api_key:
        return FreeCurrencyApiProvider(api_key=api_key, base_url=os.getenv("FX_API_URL"))
    return FallbackFxProvider()


def fx_configured() -> bool:
    return bool(os.getenv("FX_API_KEY", "").strip())


def refresh_fx_rates(base_currency: str, quote_currencies: Iterable[str]) -> int:
    quotes = set(code.upper() for code in quote_currencies if code)
    quotes.add(base_currency.upper())
    provider = get_fx_provider()
    fetched = provider.fetch_rates(base_currency=base_currency.upper(), quote_currencies=quotes)
    count = 0

    # Always keep base->base=1 for deterministic conversion.
    fetched.append(
        FxRateQuote(
            base_currency=base_currency.upper(),
            quote_currency=base_currency.upper(),
            rate=Decimal("1.0"),
            as_of=timezone.now(),
            source=provider.name,
        ),
    )

    for quote in fetched:
        FxRate.objects.update_or_create(
            base_currency=quote.base_currency,
            quote_currency=quote.quote_currency,
            defaults={
                "rate": quote.rate,
                "as_of": quote.as_of,
                "source": quote.source,
            },
        )
        count += 1
    logger.info("FX rates refreshed", extra={"count": count, "source": provider.name})
    return count


def get_rate(base_currency: str, quote_currency: str) -> Decimal:
    base = base_currency.upper()
    quote = quote_currency.upper()
    if base == quote:
        return Decimal("1.0")
    rate = (
        FxRate.objects.filter(base_currency=base, quote_currency=quote)
        .order_by("-as_of")
        .values_list("rate", flat=True)
        .first()
    )
    if rate is not None:
        return Decimal(rate)
    return Decimal("1.0")


def convert_minor_units(amount_minor: int, base_currency: str, quote_currency: str) -> int:
    if amount_minor == 0:
        return 0
    major = from_minor_units(amount_minor)
    converted = quantize_money(major * get_rate(base_currency, quote_currency))
    return to_minor_units(converted)


def convert_decimal(amount: Decimal, base_currency: str, quote_currency: str) -> Decimal:
    converted = quantize_money(Decimal(str(amount)) * get_rate(base_currency, quote_currency))
    return converted
