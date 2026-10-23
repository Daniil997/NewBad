"""Fetches delayed market observations from the official MOEX ISS API."""

import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import yaml

from app.db import ROOT


class MarketDataError(RuntimeError):
    pass


def load_market_config(path: Path | None = None) -> dict:
    config_path = path or ROOT / "config" / "market_data.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if config.get("provider") != "moex_iss" or not config.get("categories"):
        raise ValueError("Некорректный каталог MOEX ISS")
    return config


def collect_market_data(categories: list[str] | None = None, *, config: dict | None = None) -> dict:
    settings = config or load_market_config()
    catalog = settings["categories"]
    requested = categories or list(catalog)
    unknown = set(requested) - set(catalog)
    if unknown:
        raise ValueError("Неизвестная категория рыночных данных")
    started = datetime.now(timezone.utc).isoformat()
    result_categories = []
    all_items = []
    for category in requested:
        definition = catalog[category]
        instruments = definition["instruments"]
        endpoint = "/".join((settings["base_url"].rstrip("/"), "engines", definition["engine"], "markets", definition["market"], "boards", definition["board"], "securities.json"))
        params = {"iss.meta": "off", "iss.only": "marketdata,securities", "securities": ",".join(instruments)}
        url = f"{endpoint}?{urlencode(params)}"
        try:
            payload = _fetch(url, settings)
            parsed = parse_iss_payload(payload, category, instruments, url)
            all_items.extend(parsed)
            result_categories.append({"category": category, "status": "complete", "fetched": len(parsed), "missing": sorted(set(instruments) - {item["instrument"] for item in parsed}), "error": ""})
        except MarketDataError as error:
            result_categories.append({"category": category, "status": "error", "fetched": 0, "missing": instruments, "error": str(error)})
    statuses = [item["status"] for item in result_categories]
    status = "error" if statuses and all(item == "error" for item in statuses) else "partial" if "error" in statuses else "complete"
    return {"status": status, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "items": all_items, "categories": result_categories}


def parse_iss_payload(payload: dict, category: str, instruments: list[str], source_url: str = "") -> list[dict]:
    tables = {}
    for name in ("marketdata", "securities"):
        table = payload.get(name)
        if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("data"), list):
            raise MarketDataError(f"Ответ MOEX не содержит таблицу {name}")
        tables[name] = [dict(zip(table["columns"], row, strict=False)) for row in table["data"]]
    security = {row.get("SECID"): row for row in tables["securities"]}
    market = {row.get("SECID"): row for row in tables["marketdata"]}
    result = []
    observed_at = datetime.now(timezone.utc).isoformat()
    for instrument in instruments:
        quote = market.get(instrument)
        if not quote:
            continue
        info = security.get(instrument, {})
        price = _number(quote.get("LAST"))
        change = _number(quote.get("LASTTOPREVPRICE"))
        if price is None:
            continue
        trade_date = str(quote.get("TRADEDATE") or "").strip()
        update_time = str(quote.get("UPDATETIME") or "").strip()
        result.append({
            "category": category, "instrument": instrument,
            "name": str(info.get("SHORTNAME") or info.get("SECNAME") or instrument),
            "price": price, "change_pct": change, "currency": "RUB", "unit": "за лот" if category != "metals" else "за грамм/лотовый инструмент",
            "source_url": source_url, "source_timestamp": "T".join(part for part in (trade_date, update_time) if part) or observed_at,
            "observed_at": observed_at,
        })
    return result


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _fetch(url: str, settings: dict) -> dict:
    for attempt in range(3):
        try:
            response = httpx.get(url, timeout=float(settings.get("timeout_s", 15)), follow_redirects=False,
                                 headers={"User-Agent": "NewBad/0.1", "Accept": "application/json"})
            response.raise_for_status()
            if len(response.content) > int(settings.get("max_response_bytes", 5 * 1024 * 1024)):
                raise MarketDataError("Ответ MOEX превышает лимит размера")
            payload = response.json()
            if not isinstance(payload, dict):
                raise MarketDataError("MOEX вернула неожиданный формат")
            return payload
        except MarketDataError:
            raise
        except httpx.TimeoutException as error:
            if attempt == 2:
                raise MarketDataError("Истекло время ожидания MOEX") from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code < 500 or attempt == 2:
                raise MarketDataError(f"MOEX ответила HTTP {error.response.status_code}") from error
        except (httpx.HTTPError, ValueError) as error:
            if attempt == 2:
                raise MarketDataError("MOEX недоступна или вернула некорректные данные") from error
        time.sleep(0.25 * (2 ** attempt))
    raise MarketDataError("MOEX недоступна")
