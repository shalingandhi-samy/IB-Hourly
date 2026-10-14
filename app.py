import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from drax_client import fetch_inbound

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "reports.db"

KPI_NAMES = [
    "Hour Profile", "Hour %", "Goal Volume", "IB Volume", "Volume hit/miss",
    "Cumulative Volume hit/miss", "Drax Hours", "IB UPH", "Decant UPH",
    "Unload UPH", "Remedy HC",
]

SHIFTS = {
    "S1": {"name": "S1", "days": "Monday–Thursday", "start": "7:00 AM", "end": "5:30 PM", "length": "10.5 hrs", "slot_minutes": list(range(7 * 60, 17 * 60 + 1, 60))},
    "S2": {"name": "S2", "days": "Monday–Thursday", "start": "6:00 PM", "end": "4:30 AM", "length": "10.5 hrs", "slot_minutes": list(range(18 * 60, 24 * 60, 60)) + list(range(0, 4 * 60 + 1, 60))},
    "S4": {"name": "S4", "days": "Friday–Sunday", "start": "7:00 AM", "end": "7:00 PM", "length": "12 hrs", "slot_minutes": list(range(7 * 60, 19 * 60, 60))},
    "S5": {"name": "S5", "days": "Friday–Sunday", "start": "7:00 PM", "end": "7:00 AM", "length": "12 hrs", "slot_minutes": list(range(19 * 60, 24 * 60, 60)) + list(range(0, 7 * 60, 60))},
}

app = FastAPI(title="Inbound Hourly Report")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
DRAX_STATUS = {"state": "not run", "message": "Waiting for hourly refresh", "updated_at": ""}


def active_shift(now: datetime):
    weekday = now.weekday()
    minutes = now.hour * 60 + now.minute
    if weekday <= 3:
        if 7 * 60 <= minutes < 17 * 60 + 30:
            return "S1", now.date()
        if minutes >= 18 * 60:
            return "S2", now.date()
        if minutes < 4 * 60 + 30:
            return "S2", (now - timedelta(days=1)).date()
    if weekday >= 4:
        if 7 * 60 <= minutes < 19 * 60:
            return "S4", now.date()
        if minutes >= 19 * 60:
            return "S5", now.date()
        if minutes < 7 * 60:
            return "S5", (now - timedelta(days=1)).date()
    return None, now.date()


def save_drax_values(shift: str, report_date: date, values: dict[str, str], hour: int):
    connection = db()
    for kpi, value in values.items():
        connection.execute("""
            INSERT INTO hourly_kpis (report_date, shift, hour, kpi, value)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_date, shift, hour, kpi) DO UPDATE SET value=excluded.value
        """, (report_date.isoformat(), shift, hour, kpi, value))
    connection.commit()
    connection.close()


async def refresh_drax():
    now = datetime.now().replace(second=0, microsecond=0)
    shift, report_date = active_shift(now)
    if not shift:
        DRAX_STATUS.update(state="skipped", message="No active inbound shift", updated_at=now.isoformat())
        return
    start = now - timedelta(hours=1)
    try:
        values = await asyncio.to_thread(fetch_inbound, start, now)
        save_drax_values(shift, report_date, values, start.hour * 60)
        DRAX_STATUS.update(state="connected", message=f"Updated {shift}", updated_at=now.isoformat())
    except Exception as exc:
        DRAX_STATUS.update(state="error", message=str(exc), updated_at=now.isoformat())


async def drax_scheduler():
    while True:
        await refresh_drax()
        await asyncio.sleep(3600)


@app.on_event("startup")
async def start_drax_scheduler():
    app.state.drax_task = asyncio.create_task(drax_scheduler())


@app.on_event("shutdown")
async def stop_drax_scheduler():
    app.state.drax_task.cancel()



def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE IF NOT EXISTS hourly_kpis (
            report_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            hour INTEGER NOT NULL,
            kpi TEXT NOT NULL,
            value TEXT DEFAULT '',
            PRIMARY KEY (report_date, shift, hour, kpi)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shift_notes (
            report_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            block INTEGER NOT NULL,
            notes TEXT DEFAULT '',
            PRIMARY KEY (report_date, shift, block)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shift_summary (
            report_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            lp_volume TEXT DEFAULT '',
            lp_total_uph TEXT DEFAULT '',
            backlog_units TEXT DEFAULT '',
            PRIMARY KEY (report_date, shift)
        )
    """)
    return connection


def hour_rows(shift_key: str, report_date: str):
    shift = SHIFTS[shift_key]
    connection = db()
    rows = connection.execute(
        "SELECT hour, kpi, value FROM hourly_kpis WHERE report_date = ? AND shift = ? ORDER BY hour",
        (report_date, shift_key),
    ).fetchall()
    connection.close()
    saved = {}
    for row in rows:
        saved.setdefault(row["hour"], {})[row["kpi"]] = row["value"]
    result = []
    for slot in shift["slot_minutes"]:
        display_minutes = slot % (24 * 60)
        display_hour, minute = divmod(display_minutes, 60)
        hour_12 = display_hour % 12 or 12
        meridiem = "AM" if display_hour < 12 else "PM"
        label = f"{hour_12}:{minute:02d} {meridiem}"
        result.append({"hour": slot, "label": label, "kpis": saved.get(slot, {})})
    return result


def shift_totals(rows):
    total_kpis = {"Hour %", "Goal Volume", "IB Volume", "Drax Hours", "Volume hit/miss"}
    average_kpis = {"IB UPH", "Decant UPH", "Unload UPH"}
    latest_kpis = {"Cumulative Volume hit/miss", "Remedy HC"}
    totals = {}
    for kpi in KPI_NAMES:
        values = []
        for row in rows:
            raw = row["kpis"].get(kpi, "")
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                pass
        if kpi in total_kpis and values:
            result = sum(values)
        elif kpi in average_kpis and values:
            result = sum(values) / len(values)
        elif kpi in latest_kpis:
            result = next((row["kpis"].get(kpi) for row in reversed(rows) if row["kpis"].get(kpi, "") != ""), "")
        else:
            result = ""
        totals[kpi] = f"{result:g}" if isinstance(result, float) else result
    return totals


def saved_shift_summary(shift: str, report_date: str):
    connection = db()
    row = connection.execute(
        "SELECT * FROM shift_summary WHERE report_date = ? AND shift = ?",
        (report_date, shift),
    ).fetchone()
    connection.close()
    return dict(row) if row else {}


def saved_shift_notes(shift: str, report_date: str):
    connection = db()
    rows = connection.execute(
        "SELECT block, notes FROM shift_notes WHERE report_date = ? AND shift = ? ORDER BY block",
        (report_date, shift),
    ).fetchall()
    connection.close()
    return {row["block"]: row["notes"] for row in rows}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, shift: str = "", report_date: str = ""):
    selected = shift if shift in SHIFTS else ""
    chosen_date = report_date or date.today().isoformat()
    rows = hour_rows(selected, chosen_date) if selected else []
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "shifts": SHIFTS, "selected": selected,
        "report_date": chosen_date, "rows": rows, "kpi_names": KPI_NAMES,
        "shift_totals": shift_totals(rows), "shift_notes": saved_shift_notes(selected, chosen_date) if selected else {},
        "shift_summary": saved_shift_summary(selected, chosen_date) if selected else {},
        "drax_status": DRAX_STATUS,
    })


@app.post("/reports/save")
def save_report(
    shift: str = Form(...), report_date: str = Form(...),
    hours: list[int] = Form(default=[]), kpi_values: list[str] = Form(default=[]),
    block_notes: list[str] = Form(default=[]),
    lp_volume: str = Form(default=""), lp_total_uph: str = Form(default=""),
    backlog_units: str = Form(default=""),
):
    if shift not in SHIFTS:
        return RedirectResponse("/", status_code=303)
    connection = db()
    for hour_index, hour in enumerate(hours):
        for kpi_index, kpi in enumerate(KPI_NAMES):
            value_index = kpi_index * len(hours) + hour_index
            if value_index >= len(kpi_values):
                continue
            connection.execute("""
                INSERT INTO hourly_kpis (report_date, shift, hour, kpi, value)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(report_date, shift, hour, kpi) DO UPDATE SET value=excluded.value
            """, (report_date, shift, hour, kpi, kpi_values[value_index]))
    for block, notes in enumerate(block_notes, start=1):
        connection.execute("""
            INSERT INTO shift_notes (report_date, shift, block, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(report_date, shift, block) DO UPDATE SET notes=excluded.notes
        """, (report_date, shift, block, notes))
    connection.execute("""
        INSERT INTO shift_summary (report_date, shift, lp_volume, lp_total_uph, backlog_units)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(report_date, shift) DO UPDATE SET
          lp_volume=excluded.lp_volume, lp_total_uph=excluded.lp_total_uph,
          backlog_units=excluded.backlog_units
    """, (report_date, shift, lp_volume, lp_total_uph, backlog_units))
    connection.commit()
    connection.close()
    return RedirectResponse(f"/?shift={shift}&report_date={report_date}&saved=1", status_code=303)
