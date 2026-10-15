"""Fiat exchange rates, expressed as *units of currency per 1 USD*.

Order: TCMB for TRY (the official Turkish rate), ECB reference rates for the
other majors, and CoinGecko's USDT price as the last resort for anything else.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from mazecrypto.core import validate as v
from mazecrypto.providers.http import Http, NetError

HOSTS = {"www.tcmb.gov.tr": 2.0, "www.ecb.europa.eu": 2.0}
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
TCMB_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"


def _xml(text: str) -> ET.Element:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise v.ValidationError("refusing XML with DTD")
    try:
        return ET.fromstring(text.encode())
    except ET.ParseError as e:
        raise v.ValidationError("bad XML") from e


def parse_ecb(text: str) -> dict[str, float]:
    per_eur: dict[str, float] = {"EUR": 1.0}
    for el in _xml(text).iter():
        if el.tag.endswith("Cube") and "currency" in el.attrib:
            try:
                per_eur[v.fiat(el.attrib["currency"])] = v.price(el.attrib["rate"])
            except v.ValidationError:
                continue
    usd = per_eur.get("USD")
    if not usd:
        raise v.ValidationError("ECB: no USD rate")
    return {c: r / usd for c, r in per_eur.items()}


def parse_tcmb(text: str) -> dict[str, float]:
    """TRY per USD (mid of forex buying/selling)."""
    for cur in _xml(text).iter("Currency"):
        if cur.attrib.get("CurrencyCode") == "USD":
            buy = v.price(cur.findtext("ForexBuying"), "ForexBuying")
            sell = v.price(cur.findtext("ForexSelling"), "ForexSelling")
            unit = v.price(cur.findtext("Unit") or "1", "Unit")
            return {"TRY": (buy + sell) / 2 / unit, "USD": 1.0}
    raise v.ValidationError("TCMB: no USD")


async def fetch_rates(http: Http, wanted: set[str]) -> tuple[dict[str, float], list[str]]:
    """Rates for ``wanted`` currencies and the list of sources that failed."""
    rates: dict[str, float] = {"USD": 1.0}
    errors: list[str] = []
    need = {c for c in wanted if c != "USD"}
    if "TRY" in need:
        try:
            rates.update(parse_tcmb(await http.get_text(TCMB_URL)))
        except (NetError, v.ValidationError) as e:
            errors.append(f"TCMB: {e}")
    if need - rates.keys():
        try:
            ecb = parse_ecb(await http.get_text(ECB_URL))
            rates.update({k: val for k, val in ecb.items() if k not in rates})
        except (NetError, v.ValidationError) as e:
            errors.append(f"ECB: {e}")
    missing = need - rates.keys()
    if missing:
        try:
            data = v.mapping(await http.get_json("https://api.coingecko.com/api/v3/simple/price", {
                "ids": "tether", "vs_currencies": ",".join(sorted(c.lower() for c in missing))}))
            for c, r in v.mapping(data.get("tether")).items():
                rates[v.fiat(c)] = v.price(r)
        except (NetError, v.ValidationError) as e:
            errors.append(f"CoinGecko FX: {e}")
    return rates, errors
