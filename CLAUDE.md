# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Activate virtual environment (required before running)
source venv/bin/activate

# Run the development server (auto-reload on changes)
uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Alternatively, run directly
python main.py
```

The app is accessible at `http://127.0.0.1:8000`. There are no tests or linting configurations.

## Architecture

This is a single-file FastAPI timesheet app for consultant billing. All backend logic lives in `main.py` — routes, database access, and business logic together.

**Stack:** FastAPI + Uvicorn (ASGI) + SQLite + Jinja2 templates + Vanilla JS (no build step, no frontend framework)

**Database:** A single `logs` table in `timesheet.db` (auto-created on startup by `init_db()`). The connection helper `get_db_connection()` uses `sqlite3.Row` for dict-like row access.

**Timer workflow:**
1. `POST /start` — inserts a row with `start_time` set and `end_time=NULL` (only one active timer allowed at a time)
2. Browser JS displays a live countdown using the stored `start_time`
3. `POST /stop` — user fills in client/task/description; server calculates hours and updates the row

**Key routes:**
- `GET /` — home page: shows active timer or start form, plus monthly log table
- `GET /report` — billing summary grouped hierarchically by client → task, with date range filtering
- `GET /download_csv` — exports current month's logs as a CSV download
- `GET|POST /edit/{log_id}`, `POST /delete/{log_id}` — CRUD for individual entries

**Templates** (`templates/`) correspond 1:1 with page views. All styles are inline in the HTML — `static/style.css` is empty.
