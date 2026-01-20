import sqlite3
from datetime import datetime, date
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import csv
import io
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB_FILE = 'timesheet.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Create table with new columns for timer
    c.execute('''CREATE TABLE IF NOT EXISTS logs 
                 (id INTEGER PRIMARY KEY, 
                  log_date DATE, 
                  client TEXT, 
                  task TEXT, 
                  details TEXT, 
                  start_time TIMESTAMP,
                  end_time TIMESTAMP,
                  hours REAL)''')
    
    # Simple migration: Check if start_time exists, if not, add it (for existing users)
    try:
        c.execute("SELECT start_time FROM logs LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE logs ADD COLUMN start_time TIMESTAMP")
        c.execute("ALTER TABLE logs ADD COLUMN end_time TIMESTAMP")
    
    conn.commit()
    conn.close()

init_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Check if there is an ACTIVE timer (end_time is NULL)
    c.execute("SELECT * FROM logs WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    active_log = c.fetchone()
    
    # 2. Get history for the current month
    current_month = date.today().strftime("%Y-%m")
    c.execute("SELECT * FROM logs WHERE strftime('%Y-%m', log_date) = ? AND end_time IS NOT NULL ORDER BY log_date DESC, start_time DESC", (current_month,))
    logs = c.fetchall()
    
    conn.close()
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "logs": logs, 
        "active_log": active_log,
        "today": date.today()
    })

@app.post("/start")
async def start_timer(client: str = Form(...), task: str = Form(...)):
    """Starts a timer by creating a row with start_time but no end_time"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Prevent starting if one is already running
    c.execute("SELECT id FROM logs WHERE end_time IS NULL")
    if c.fetchone():
        return RedirectResponse(url="/", status_code=303)

    now = datetime.now()
    log_date = now.strftime("%Y-%m-%d")
    
    c.execute("INSERT INTO logs (log_date, client, task, start_time) VALUES (?, ?, ?, ?)",
              (log_date, client, task, now))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.post("/stop")
async def stop_timer(log_id: int = Form(...), details: str = Form(...)):
    """Stops the timer: sets end_time and calculates hours"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT start_time FROM logs WHERE id = ?", (log_id,))
    row = c.fetchone()
    if row:
        start_time = datetime.fromisoformat(str(row['start_time']))
        end_time = datetime.now()
        
        # Calculate hours (decimal)
        duration = end_time - start_time
        hours = round(duration.total_seconds() / 3600, 2)
        
        c.execute("""UPDATE logs 
                     SET end_time = ?, details = ?, hours = ? 
                     WHERE id = ?""", 
                  (end_time, details, hours, log_id))
        conn.commit()
    
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.get("/edit/{log_id}", response_class=HTMLResponse)
async def edit_log_form(request: Request, log_id: int):
    """Display edit form for a specific log entry"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM logs WHERE id = ?", (log_id,))
    log = c.fetchone()
    conn.close()

    if not log:
        return RedirectResponse(url="/", status_code=303)

    log_dict = dict(log)

    if log['start_time']:
        start_dt = datetime.fromisoformat(str(log['start_time']))
        log_dict['start_time_only'] = start_dt.strftime('%H:%M')
        log_dict['start_date'] = start_dt.strftime('%Y-%m-%d')

    if log['end_time']:
        end_dt = datetime.fromisoformat(str(log['end_time']))
        log_dict['end_time_only'] = end_dt.strftime('%H:%M')
    else:
        log_dict['end_time_only'] = ''

    return templates.TemplateResponse("edit.html", {
        "request": request,
        "log": log_dict
    })

@app.post("/edit/{log_id}")
async def edit_log_submit(
    log_id: int,
    log_date: str = Form(...),
    client: str = Form(...),
    task: str = Form(...),
    details: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form("")
):
    """Process edit form submission and update the log"""
    conn = get_db_connection()
    c = conn.cursor()

    start_datetime = datetime.fromisoformat(f"{log_date}T{start_time}:00")

    if end_time:
        end_datetime = datetime.fromisoformat(f"{log_date}T{end_time}:00")

        if end_datetime <= start_datetime:
            conn.close()
            return RedirectResponse(url="/", status_code=303)

        duration = end_datetime - start_datetime
        hours = round(duration.total_seconds() / 3600, 2)

        c.execute("""UPDATE logs
                     SET log_date = ?, client = ?, task = ?, details = ?,
                         start_time = ?, end_time = ?, hours = ?
                     WHERE id = ?""",
                  (log_date, client, task, details, start_datetime, end_datetime, hours, log_id))
    else:
        c.execute("""UPDATE logs
                     SET log_date = ?, client = ?, task = ?, details = ?,
                         start_time = ?, end_time = NULL, hours = NULL
                     WHERE id = ?""",
                  (log_date, client, task, details, start_datetime, log_id))

    conn.commit()
    conn.close()

    return RedirectResponse(url="/", status_code=303)

@app.post("/delete/{log_id}")
async def delete_log(log_id: int):
    """Delete a log entry"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/", status_code=303)

import calendar # Add this to your top imports

@app.get("/report", response_class=HTMLResponse)
async def generate_report(request: Request, start_date: str = None, end_date: str = None):
    conn = get_db_connection()
    c = conn.cursor()

    # Logic: Set defaults if dates are missing
    if not start_date or not end_date:
        today = date.today()
        # Default Start: 1st of current month
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
        # Default End: Last day of current month
        last_day_num = calendar.monthrange(today.year, today.month)[1]
        end_date = today.replace(day=last_day_num).strftime("%Y-%m-%d")

    # SQL: Filter by specific date range
    # We order by Client -> Task -> Date for the nested view
    query = """
        SELECT * FROM logs
        WHERE log_date BETWEEN ? AND ?
        AND hours IS NOT NULL
        ORDER BY client, task, log_date
    """
    c.execute(query, (start_date, end_date))
    logs = c.fetchall()
    conn.close()

    # --- Grouping Logic (Client > Task) ---
    summary = {}
    grand_total = 0

    for log in logs:
        client = log['client']
        task = log['task']
        hours = log['hours']

        if client not in summary:
            summary[client] = {"tasks": {}, "total": 0}

        if task not in summary[client]["tasks"]:
            summary[client]["tasks"][task] = 0

        summary[client]["tasks"][task] += hours
        summary[client]["total"] += hours
        grand_total += hours

    # Rounding
    grand_total = round(grand_total, 2)
    for client in summary:
        summary[client]["total"] = round(summary[client]["total"], 2)
        for task in summary[client]["tasks"]:
            summary[client]["tasks"][task] = round(summary[client]["tasks"][task], 2)

    return templates.TemplateResponse("report.html", {
        "request": request,
        "logs": logs,
        "summary": summary,
        "total": grand_total,
        "start_date": start_date, # Pass back to template for display
        "end_date": end_date
    })


@app.get("/download_csv")
async def download_csv():
    """Generates a CSV file of all logs for the current month"""
    conn = get_db_connection()
    c = conn.cursor()
    month = date.today().strftime("%Y-%m")
    
    # Get completed logs
    c.execute("SELECT log_date, client, task, details, start_time, end_time, hours FROM logs WHERE strftime('%Y-%m', log_date) = ? AND hours IS NOT NULL", (month,))
    logs = c.fetchall()
    conn.close()

    # Create CSV in memory
    stream = io.StringIO()
    csv_writer = csv.writer(stream)
    
    # Write Headers
    csv_writer.writerow(["Date", "Client", "Task", "Details", "Start Time", "End Time", "Hours"])
    
    # Write Rows
    for log in logs:
        csv_writer.writerow([
            log["log_date"], 
            log["client"], 
            log["task"], 
            log["details"], 
            log["start_time"], 
            log["end_time"], 
            log["hours"]
        ])
    
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=timesheet_{month}.csv"
    return response

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
