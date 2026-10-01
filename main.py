import sqlite3
from datetime import datetime, date, timedelta
import calendar
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
async def home(request: Request, month: str = None):
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Check if there is an ACTIVE timer (end_time is NULL)
    c.execute("SELECT * FROM logs WHERE end_time IS NULL ORDER BY id DESC LIMIT 1")
    active_log = c.fetchone()
    
    # 2. Get distinct months present in logs (for dropdown/navigation)
    c.execute("SELECT DISTINCT strftime('%Y-%m', log_date) FROM logs WHERE log_date IS NOT NULL AND log_date != '' ORDER BY 1 DESC")
    available_months = [r[0] for r in c.fetchall() if r[0]]
    
    current_month = date.today().strftime("%Y-%m")
    if current_month not in available_months:
        available_months.insert(0, current_month)
        
    # Validate requested month
    if not month or month not in available_months:
        if month:
            try:
                datetime.strptime(month, "%Y-%m")
                selected_month = month
                if selected_month not in available_months:
                    available_months.append(selected_month)
                    available_months.sort(reverse=True)
            except ValueError:
                selected_month = current_month
        else:
            selected_month = current_month
    else:
        selected_month = month
        
    try:
        selected_month_date = datetime.strptime(selected_month, "%Y-%m")
    except ValueError:
        selected_month = current_month
        selected_month_date = datetime.strptime(selected_month, "%Y-%m")

    selected_month_name = selected_month_date.strftime("%B %Y")
    
    # Calculate prev and next months
    sy, sm = selected_month_date.year, selected_month_date.month
    prev_y, prev_m = (sy - 1, 12) if sm == 1 else (sy, sm - 1)
    next_y, next_m = (sy + 1, 1) if sm == 12 else (sy, sm + 1)
    prev_month = f"{prev_y:04d}-{prev_m:02d}"
    next_month = f"{next_y:04d}-{next_m:02d}"
    
    # 3. Get history for the selected month
    c.execute("""
        SELECT * FROM logs 
        WHERE strftime('%Y-%m', log_date) = ? AND end_time IS NOT NULL 
        ORDER BY log_date DESC, start_time DESC, id DESC
    """, (selected_month,))
    logs = c.fetchall()
    
    # Calculate total hours for selected month
    month_total_hours = round(sum(log['hours'] or 0 for log in logs), 2)
    
    # 4. Get distinct clients for datalist autocomplete
    c.execute("SELECT DISTINCT TRIM(client) FROM logs WHERE client IS NOT NULL AND TRIM(client) != '' ORDER BY 1")
    clients = [r[0] for r in c.fetchall()]
    
    conn.close()
    
    # Format month options for select dropdown
    month_options = []
    for m_str in available_months:
        m_dt = datetime.strptime(m_str, "%Y-%m")
        month_options.append({
            "value": m_str,
            "label": m_dt.strftime("%B %Y")
        })
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "logs": logs, 
        "active_log": active_log,
        "today": date.today(),
        "selected_month": selected_month,
        "selected_month_name": selected_month_name,
        "prev_month": prev_month,
        "next_month": next_month,
        "is_current_month": (selected_month == current_month),
        "month_total_hours": month_total_hours,
        "month_options": month_options,
        "clients": clients
    })

@app.post("/start")
async def start_timer(client: str = Form(...), task: str = Form(...)):
    """Starts a timer by creating a row with start_time but no end_time"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Prevent starting if one is already running
    c.execute("SELECT id FROM logs WHERE end_time IS NULL")
    if c.fetchone():
        conn.close()
        return RedirectResponse(url="/", status_code=303)

    now = datetime.now()
    log_date = now.strftime("%Y-%m-%d")
    
    c.execute("INSERT INTO logs (log_date, client, task, start_time) VALUES (?, ?, ?, ?)",
              (log_date, client.strip(), task.strip(), now))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/", status_code=303)

@app.post("/stop")
async def stop_timer(log_id: int = Form(...), details: str = Form(...)):
    """Stops the timer: sets end_time and calculates hours"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT log_date, start_time FROM logs WHERE id = ?", (log_id,))
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
        log_month = row['log_date'][:7] if row['log_date'] else None
    else:
        log_month = None
    
    conn.close()
    redirect_url = f"/?month={log_month}" if log_month else "/"
    return RedirectResponse(url=redirect_url, status_code=303)

@app.post("/manual")
async def add_manual_entry(
    log_date: str = Form(...),
    client: str = Form(...),
    task: str = Form(...),
    details: str = Form(...),
    hours: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form("")
):
    """Manually log past time for any date without live timer"""
    conn = get_db_connection()
    c = conn.cursor()
    
    parsed_hours = None
    if hours and hours.strip():
        try:
            parsed_hours = round(float(hours.strip()), 2)
        except ValueError:
            pass

    start_datetime = None
    if start_time and start_time.strip():
        start_datetime = datetime.fromisoformat(f"{log_date}T{start_time.strip()}:00")

    end_datetime = None
    if end_time and end_time.strip():
        end_datetime = datetime.fromisoformat(f"{log_date}T{end_time.strip()}:00")
        if start_datetime and end_datetime <= start_datetime:
            end_datetime = end_datetime + timedelta(days=1)

    if parsed_hours is not None:
        final_hours = parsed_hours
        if start_datetime and not end_datetime:
            end_datetime = start_datetime + timedelta(hours=final_hours)
    elif start_datetime and end_datetime:
        duration = end_datetime - start_datetime
        final_hours = round(duration.total_seconds() / 3600, 2)
    else:
        final_hours = 0.0

    if not start_datetime:
        start_datetime = datetime.fromisoformat(f"{log_date}T09:00:00")
        if not end_datetime:
            end_datetime = start_datetime + timedelta(hours=final_hours)

    c.execute("""
        INSERT INTO logs (log_date, client, task, details, start_time, end_time, hours)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (log_date, client.strip(), task.strip(), details.strip(), start_datetime, end_datetime, final_hours))
    conn.commit()
    conn.close()

    month = log_date[:7]
    return RedirectResponse(url=f"/?month={month}", status_code=303)

@app.get("/edit/{log_id}", response_class=HTMLResponse)
async def edit_log_form(
    request: Request,
    log_id: int,
    return_to: str = None,
    month: str = None,
    start_date: str = None,
    end_date: str = None
):
    """Display edit form for a specific log entry"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM logs WHERE id = ?", (log_id,))
    log = c.fetchone()
    
    # Fetch distinct clients for autocomplete datalist
    c.execute("SELECT DISTINCT TRIM(client) FROM logs WHERE client IS NOT NULL AND TRIM(client) != '' ORDER BY 1")
    clients = [r[0] for r in c.fetchall()]
    conn.close()

    if not log:
        return RedirectResponse(url="/", status_code=303)

    log_dict = dict(log)

    if log['start_time']:
        start_dt = datetime.fromisoformat(str(log['start_time']))
        log_dict['start_time_only'] = start_dt.strftime('%H:%M')
        log_dict['start_date'] = start_dt.strftime('%Y-%m-%d')
    else:
        log_dict['start_time_only'] = ''
        log_dict['start_date'] = log['log_date']

    if log['end_time']:
        end_dt = datetime.fromisoformat(str(log['end_time']))
        log_dict['end_time_only'] = end_dt.strftime('%H:%M')
    else:
        log_dict['end_time_only'] = ''

    # Determine back URL to return to correct view
    if return_to == "report" and start_date and end_date:
        back_url = f"/report?start_date={start_date}&end_date={end_date}"
    elif month:
        back_url = f"/?month={month}"
    elif log['log_date']:
        back_url = f"/?month={log['log_date'][:7]}"
    else:
        back_url = "/"

    return templates.TemplateResponse("edit.html", {
        "request": request,
        "log": log_dict,
        "clients": clients,
        "return_to": return_to or "",
        "month": month or (log['log_date'][:7] if log['log_date'] else ""),
        "start_date": start_date or "",
        "end_date": end_date or "",
        "back_url": back_url
    })

@app.post("/edit/{log_id}")
async def edit_log_submit(
    log_id: int,
    log_date: str = Form(...),
    client: str = Form(...),
    task: str = Form(...),
    details: str = Form(...),
    start_time: str = Form(""),
    end_time: str = Form(""),
    hours: str = Form(""),
    return_to: str = Form(""),
    month: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form("")
):
    """Process edit form submission and update the log"""
    conn = get_db_connection()
    c = conn.cursor()

    parsed_hours = None
    if hours and hours.strip():
        try:
            parsed_hours = round(float(hours.strip()), 2)
        except ValueError:
            pass

    start_datetime = None
    if start_time and start_time.strip():
        start_datetime = datetime.fromisoformat(f"{log_date}T{start_time.strip()}:00")

    end_datetime = None
    if end_time and end_time.strip():
        end_datetime = datetime.fromisoformat(f"{log_date}T{end_time.strip()}:00")
        if start_datetime and end_datetime <= start_datetime:
            # Task ran overnight / crossed midnight
            end_datetime = end_datetime + timedelta(days=1)

    if parsed_hours is not None and parsed_hours >= 0:
        final_hours = parsed_hours
        if start_datetime and not end_datetime:
            end_datetime = start_datetime + timedelta(hours=final_hours)
    elif start_datetime and end_datetime:
        duration = end_datetime - start_datetime
        final_hours = round(duration.total_seconds() / 3600, 2)
    else:
        final_hours = None
        end_datetime = None

    c.execute("""UPDATE logs
                 SET log_date = ?, client = ?, task = ?, details = ?,
                     start_time = ?, end_time = ?, hours = ?
                 WHERE id = ?""",
              (log_date, client.strip(), task.strip(), details.strip(), start_datetime, end_datetime, final_hours, log_id))

    conn.commit()
    conn.close()

    if return_to == "report" and start_date and end_date:
        return RedirectResponse(url=f"/report?start_date={start_date}&end_date={end_date}", status_code=303)
    elif month:
        return RedirectResponse(url=f"/?month={month}", status_code=303)
    else:
        return RedirectResponse(url=f"/?month={log_date[:7]}", status_code=303)

@app.post("/delete/{log_id}")
async def delete_log(
    log_id: int,
    return_to: str = Form(""),
    month: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form("")
):
    """Delete a log entry and redirect back to origin view"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT log_date FROM logs WHERE id = ?", (log_id,))
    row = c.fetchone()
    log_month = row['log_date'][:7] if row and row['log_date'] else None

    c.execute("DELETE FROM logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()

    if return_to == "report" and start_date and end_date:
        return RedirectResponse(url=f"/report?start_date={start_date}&end_date={end_date}", status_code=303)
    elif month:
        return RedirectResponse(url=f"/?month={month}", status_code=303)
    elif log_month:
        return RedirectResponse(url=f"/?month={log_month}", status_code=303)
    return RedirectResponse(url="/", status_code=303)

@app.get("/report", response_class=HTMLResponse)
async def generate_report(request: Request, start_date: str = None, end_date: str = None):
    conn = get_db_connection()
    c = conn.cursor()

    today = date.today()
    curr_month_start = today.replace(day=1).strftime("%Y-%m-%d")
    curr_last_day = calendar.monthrange(today.year, today.month)[1]
    curr_month_end = today.replace(day=curr_last_day).strftime("%Y-%m-%d")

    # Previous month range for quick filters
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_last_day = calendar.monthrange(prev_y, prev_m)[1]
    prev_month_start = f"{prev_y:04d}-{prev_m:02d}-01"
    prev_month_end = f"{prev_y:04d}-{prev_m:02d}-{prev_last_day:02d}"

    # Default Start and End: Current month if not provided
    if not start_date or not end_date:
        start_date = curr_month_start
        end_date = curr_month_end

    # SQL: Filter by specific date range
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
        client = (log['client'] or '').strip()
        task = (log['task'] or '').strip()
        hours = log['hours'] or 0

        if client not in summary:
            summary[client] = {"tasks": {}, "total": 0}

        if task not in summary[client]["tasks"]:
            summary[client]["tasks"][task] = 0

        summary[client]["tasks"][task] += hours
        summary[client]["total"] += hours
        grand_total += hours

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
        "start_date": start_date,
        "end_date": end_date,
        "curr_month_start": curr_month_start,
        "curr_month_end": curr_month_end,
        "prev_month_start": prev_month_start,
        "prev_month_end": prev_month_end
    })

@app.get("/download_csv")
async def download_csv(month: str = None):
    """Generates a CSV file of all logs for the specified or current month"""
    conn = get_db_connection()
    c = conn.cursor()
    if not month:
        month = date.today().strftime("%Y-%m")
    
    # Get completed logs for selected month
    c.execute("""
        SELECT log_date, client, task, details, start_time, end_time, hours 
        FROM logs 
        WHERE strftime('%Y-%m', log_date) = ? AND hours IS NOT NULL 
        ORDER BY log_date DESC, start_time DESC, id DESC
    """, (month,))
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
