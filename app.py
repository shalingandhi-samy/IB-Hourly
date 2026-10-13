from datetime import date
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

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


@app.get("/", response_class=HTMLResponse)
def home(request: Request, shift: str = "", report_date: str = ""):
    selected = shift if shift in SHIFTS else ""
    chosen_date = report_date or date.today().isoformat()
    rows = hour_rows(selected, chosen_date) if selected else []
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "shifts": SHIFTS, "selected": selected,
        "report_date": chosen_date, "rows": rows, "kpi_names": KPI_NAMES,
    })


@app.post("/reports/save")
def save_report(
    shift: str = Form(...), report_date: str = Form(...),
    hours: list[int] = Form(default=[]), kpi_values: list[str] = Form(default=[]),
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
    connection.commit()
    connection.close()
    return RedirectResponse(f"/?shift={shift}&report_date={report_date}&saved=1", status_code=303)
