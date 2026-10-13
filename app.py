from datetime import date
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "reports.db"

SHIFTS = {
    "S1": {"name": "S1", "days": "Monday–Thursday", "start": "7:00 AM", "end": "5:30 PM", "length": "10.5 hrs", "slot_minutes": list(range(7 * 60, 17 * 60 + 31, 60)) + [17 * 60 + 30]},
    "S2": {"name": "S2", "days": "Monday–Thursday", "start": "6:00 PM", "end": "4:30 AM", "length": "10.5 hrs", "slot_minutes": list(range(18 * 60, 24 * 60, 60)) + list(range(0, 4 * 60 + 31, 60)) + [4 * 60 + 30]},
    "S4": {"name": "S4", "days": "Friday–Sunday", "start": "7:00 AM", "end": "7:00 PM", "length": "12 hrs", "slot_minutes": list(range(7 * 60, 19 * 60 + 1, 60))},
    "S5": {"name": "S5", "days": "Friday–Sunday", "start": "7:00 PM", "end": "7:00 AM", "length": "12 hrs", "slot_minutes": list(range(19 * 60, 24 * 60, 60)) + list(range(0, 7 * 60 + 1, 60))},
}

app = FastAPI(title="Inbound Hourly Report")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE IF NOT EXISTS hourly_reports (
            report_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            hour INTEGER NOT NULL,
            cases TEXT DEFAULT '',
            pallets TEXT DEFAULT '',
            labor TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            PRIMARY KEY (report_date, shift, hour)
        )
    """)
    return connection


def hour_rows(shift_key: str, report_date: str):
    shift = SHIFTS[shift_key]
    connection = db()
    rows = connection.execute(
        "SELECT * FROM hourly_reports WHERE report_date = ? AND shift = ? ORDER BY hour",
        (report_date, shift_key),
    ).fetchall()
    connection.close()
    saved = {row["hour"]: row for row in rows}
    result = []
    for slot in shift["slot_minutes"]:
        display_minutes = slot % (24 * 60)
        display_hour, minute = divmod(display_minutes, 60)
        hour_12 = display_hour % 12 or 12
        meridiem = "AM" if display_hour < 12 else "PM"
        label = f"{hour_12}:{minute:02d} {meridiem}"
        result.append({"hour": slot, "label": label, **dict(saved.get(slot, {}))})
    return result


@app.get("/", response_class=HTMLResponse)
def home(request: Request, shift: str = "", report_date: str = ""):
    selected = shift if shift in SHIFTS else ""
    chosen_date = report_date or date.today().isoformat()
    rows = hour_rows(selected, chosen_date) if selected else []
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "shifts": SHIFTS, "selected": selected,
        "report_date": chosen_date, "rows": rows,
    })


@app.post("/reports/save")
def save_report(
    shift: str = Form(...), report_date: str = Form(...),
    hours: list[int] = Form(default=[]), cases: list[str] = Form(default=[]),
    pallets: list[str] = Form(default=[]), labor: list[str] = Form(default=[]),
    notes: list[str] = Form(default=[]),
):
    if shift not in SHIFTS:
        return RedirectResponse("/", status_code=303)
    connection = db()
    for index, hour in enumerate(hours):
        values = (report_date, shift, hour, cases[index], pallets[index], labor[index], notes[index])
        connection.execute("""
            INSERT INTO hourly_reports (report_date, shift, hour, cases, pallets, labor, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, shift, hour) DO UPDATE SET
              cases=excluded.cases, pallets=excluded.pallets,
              labor=excluded.labor, notes=excluded.notes
        """, values)
    connection.commit()
    connection.close()
    return RedirectResponse(f"/?shift={shift}&report_date={report_date}&saved=1", status_code=303)
