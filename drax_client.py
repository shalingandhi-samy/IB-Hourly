"""Authenticated Drax building-overview client."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from requests_ntlm import HttpNtlmAuth

URL = "https://drax.walmart.com/building_overview/"


def _number(value: str) -> str:
    cleaned = value.replace(",", "").replace("%", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return match.group(0) if match else ""


def _session() -> requests.Session:
    session = requests.Session()
    cookie = os.getenv("DRAX_SESSION_COOKIE")
    if cookie:
        session.cookies.set("sessionid", cookie, domain="drax.walmart.com")
    else:
        session.auth = HttpNtlmAuth("", "", send_cbt=False)
    session.headers["User-Agent"] = "IB-Hourly/1.0"
    return session


def _table_rows(soup: BeautifulSoup, table_id: str):
    table = soup.find("table", id=table_id)
    if not table:
        return []
    headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
    result = []
    for tr in table.select("tbody tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
        if cells:
            result.append(dict(zip(headers, cells)))
    return result


def fetch_inbound(start: datetime, end: datetime) -> dict[str, str]:
    params = {
        "date_hour_after": start.strftime("%Y-%m-%d %H:%M"),
        "date_hour_before": end.strftime("%Y-%m-%d %H:%M"),
    }
    response = _session().get(URL, params=params, timeout=30, verify=False)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    if not soup.find(id="total_building_table"):
        raise RuntimeError("Drax response did not contain the building overview table")

    area_rows = _table_rows(soup, "total_building_table")
    department_rows = _table_rows(soup, "dept_table")

    inbound = next((row for row in area_rows if "Inbound" in " ".join(row.values())), None)
    if not inbound:
        raise RuntimeError("Inbound row was not found in Drax building overview")

    decant = next(
        (row for row in department_rows
         if "018156157" in " ".join(row.values()) or "Decant" in " ".join(row.values())),
        None,
    )
    if not decant:
        raise RuntimeError("Decant 018156157 row was not found in Drax department table")

    def field(row, name):
        key = next((key for key in row if name.lower() in key.lower()), "")
        return _number(row.get(key, "")) if key else ""

    return {
        "IB Volume": field(inbound, "Volume"),
        "Drax Hours": field(inbound, "Hours"),
        "IB UPH": field(inbound, "UPH"),
        "Decant UPH": field(decant, "UPH"),
    }
