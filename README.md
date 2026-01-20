# Project Overview

A web-based timesheet application built with FastAPI for consultants to track billable hours. Features a timer-based workflow where users start/stop tasks and generate billing reports.

## Technology Stack

- **Backend**: FastAPI with Uvicorn
- **Database**: SQLite (timesheet.db)
- **Templates**: Jinja2
- **Frontend**: Vanilla HTML/CSS with minimal JavaScript

## Development Commands

### Running the Application
```bash
# Activate virtual environment
source venv/bin/activate

# Run the development server
python main.py
# or
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

The app runs on `http://127.0.0.1:8000`

### Virtual Environment
```bash
# The venv directory already exists
source venv/bin/activate

# Dependencies (if recreating environment):
# FastAPI, Uvicorn, Jinja2 (exact versions not specified in repo)
```

## Application Architecture

### Core Workflow
1. **Start Timer**: User selects client/task → creates DB record with `start_time`, `end_time=NULL`
2. **Active Timer**: Frontend displays live countdown, prevents starting new timers
3. **Stop Timer**: User adds work details → calculates hours, saves `end_time` and `details`
4. **Reports**: Generate summaries grouped by client → task with date range filtering

### Database Schema
Single table `logs` with the following structure:
- `id`: Primary key
- `log_date`: DATE when work was performed
- `client`: Client name (TEXT)
- `task`: Task description (TEXT)
- `details`: Work details added when stopping timer (TEXT)
- `start_time`: TIMESTAMP when timer started
- `end_time`: TIMESTAMP when timer stopped (NULL = active timer)
- `hours`: Calculated decimal hours (REAL)

**Important**: The schema includes a migration check that adds `start_time`/`end_time` columns if they don't exist (for backwards compatibility).

### Key Routes
- `GET /`: Home page showing active timer OR start form + current month's completed logs
- `POST /start`: Starts timer (prevents duplicate active timers)
- `POST /stop`: Stops timer, calculates hours from duration
- `GET /report`: Generates billing summary with date range filters (defaults to current month)
- `GET /download_csv`: Exports current month's data as CSV

### Database Connection Pattern
All routes use the same pattern:
```python
conn = get_db_connection()  # Creates connection with Row factory
c = conn.cursor()
# ... queries ...
conn.close()
```

### Timer State Logic
- **Active timer check**: `SELECT * FROM logs WHERE end_time IS NULL`
- Only ONE timer can be active at a time
- Hours calculated as: `round((end_time - start_time).total_seconds() / 3600, 2)`

### Report Grouping
The `/report` endpoint groups data hierarchically:
```
summary = {
    "Client A": {
        "tasks": {"Task 1": 5.5, "Task 2": 3.2},
        "total": 8.7
    }
}
```

## File Structure

```
/
├── main.py              # Single-file FastAPI application (all routes + DB logic)
├── templates/
│   ├── index.html       # Home page (start/stop timer, monthly log table)
│   └── report.html      # Billing report with client/task summary
├── static/
│   └── style.css        # Empty (styles are inline in templates)
├── timesheet.db         # SQLite database (auto-created on first run)
└── venv/                # Python 3.12 virtual environment
```

## Important Patterns

### Preventing Race Conditions
Before starting a timer, check for existing active timers:
```python
c.execute("SELECT id FROM logs WHERE end_time IS NULL")
if c.fetchone():
    return RedirectResponse(url="/", status_code=303)
```

### Date Filtering
- Month filtering: `strftime('%Y-%m', log_date) = '2024-12'`
- Range filtering: `log_date BETWEEN ? AND ?`
- Always exclude incomplete logs: `WHERE hours IS NOT NULL` or `WHERE end_time IS NOT NULL`

### CSV Export
Uses `io.StringIO()` with `StreamingResponse` for in-memory CSV generation without temporary files.

## Template Context Variables

### index.html
- `logs`: Completed logs for current month
- `active_log`: Currently running timer (None if no active timer)
- `today`: Current date object

### report.html
- `logs`: All logs in date range
- `summary`: Nested dict (client → tasks → hours)
- `total`: Grand total hours
- `start_date`/`end_date`: Filter values

## Frontend Features

### Live Timer (index.html)
JavaScript interval updates timer display every second using `active_log.start_time` as reference.

### Client Autocomplete
`<datalist>` provides suggestions for client names (currently hardcoded: "Adfluence", "Chainscript labs"). Consider dynamically populating from DB.

### Print Functionality (report.html)
CSS `@media print` hides controls when printing to PDF.

## Database Initialization

`init_db()` runs on module load:
- Creates `logs` table if not exists
- Performs migration to add timer columns if upgrading from older schema
- Safe to run multiple times (idempotent)
