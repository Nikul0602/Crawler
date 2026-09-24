# CrawlMaster — Implementation Summary

This document summarizes the full implementation of the disabled pages (**Reports**, **Schedules**, and **Settings**), atomic storage architecture, scheduler engine, and auxiliary features in CrawlMaster as specified in [`implementation_plan.md`](../plans/implementation_plan.md).

---

## 1. Core & Atomic Storage Architecture

* **`backend/paths.py`**: Added `DATA_DIR`, `SCHEDULES_FILE`, and `SETTINGS_FILE` constants.
* **`backend/services/storage.py`**: Built `write_json_atomic` and `read_json_safe`:
  * Per-file `threading.Lock` serialization to guard concurrent writes in a single process.
  * Unique temporary filenames (`<file>_<uuid>.tmp`) to avoid collision between threads.
  * Flushing and `os.fsync` before an atomic `os.replace` to ensure zero file corruption on crashes or sudden power loss.

---

## 2. Settings Service & Runtime Precedence

* **`backend/services/settings_manager.py`**:
  * Persistent storage in `data/settings.json`.
  * **Secret Redaction**: `jina_api_key` is masked as `sk-••••••••` in `get_settings()`. When updating settings, if the masked value is sent back, the stored secret is retained.
  * **Precedence Resolution**: `Explicit Request Overrides > Saved User Settings > Factory Defaults`.
  * Provides `get_effective_pipeline_config()` and `get_effective_crawler_config()` for internal crawler orchestration.

---

## 3. Report Management & Safe File APIs

* **`backend/services/report_manager.py`**:
  * **Fast Summary Path**: Reads `summary.json` if available; otherwise uses a lightweight scalar parser caching results by `data.json` mtime without loading full page arrays into memory.
  * **Path Traversal Guard**: Strict regex validation (`^[a-zA-Z0-9._-]+$`) and `candidate.is_relative_to(OUTPUT_DIR)`.
  * **Active Crawl Locking**: Deleting a report is rejected with HTTP 409 if a crawl for that domain is currently `Running`.
  * **Paginated Pages**: `get_pages(domain, offset, limit, query)` keeps responses bounded and fast.
  * **CSV Streaming**: Streams `pages.csv` on the fly with spreadsheet formula-injection prevention (prefixing cells starting with `=`, `+`, `-`, `@` with an apostrophe).

---

## 4. Schedule Service & Lifespan Scheduler

* **`backend/services/schedule_manager.py`**:
  * Manages schedules in `data/schedules.json`.
  * Recurrence calculation for `daily`, `weekly`, `monthly`, and custom cron (with `croniter` support).
  * **Atomic Claim Lock**: Advances `next_run_at` and marks `last_task_id` before crawl dispatch to prevent duplicate executions across ticks.
  * Provides CRUD, active/paused toggle, and instant `run-now` execution.

---

## 5. Enhanced Task Lifecycle & Re-run Capability

* **`backend/services/task_manager.py`**:
  * Switched to `write_json_atomic` for all updates.
  * Persists `request_config` on task creation to enable reproducible re-runs.
  * Stores `report_domain` upon task completion to link tasks directly to generated reports.
  * Added `delete_task` (safeguarded against active tasks) and `rerun_task`.

---

## 6. Backend API Server & Async Background Loop

* **`backend/api/server.py`**:
  * **Lifespan Scheduler**: FastAPI lifespan starts a non-blocking 60-second periodic scheduler loop running crawl workers on worker threads (`asyncio.to_thread`).
  * **Tasks API**:
    * `GET /api/tasks` — List all tasks.
    * `GET /api/tasks/{task_id}` & `/api/status/{task_id}` — Single task status.
    * `POST /api/tasks/{task_id}/rerun` — Re-run with original configuration snapshot.
    * `DELETE /api/tasks/{task_id}` — Delete completed or failed task.
  * **Reports API**:
    * `GET /api/reports` — List lightweight summary records.
    * `GET /api/reports/{domain}` — Fetch report metadata and first 50 pages.
    * `GET /api/reports/{domain}/pages` — Paginated pages with query filter.
    * `GET /api/reports/{domain}/download` — Stream `markdown`, `json`, or `csv`.
    * `DELETE /api/reports/{domain}` — Safely delete report.
  * **Schedules API**:
    * `GET /api/schedules` & `POST /api/schedules` — List and create schedules.
    * `PUT /api/schedules/{id}` & `DELETE /api/schedules/{id}` — Update and delete.
    * `POST /api/schedules/{id}/toggle` — Pause/activate.
    * `POST /api/schedules/{id}/run-now` — Trigger immediate crawl.
  * **Settings & System API**:
    * `GET /api/settings`, `PUT /api/settings`, `POST /api/settings/reset`.
    * `GET /api/system/storage` — Report count, directory sizes (MB), checkpoint status.
    * `POST /api/system/clear-checkpoint` — Remove root `crawl_state.json`.
  * **Page Routes**: Serves `/`, `/crawls`, `/reports`, `/schedules`, and `/settings`.

---

## 7. Frontend Pages & Modular Scripts

* **`frontend/reports.html` & `frontend/reports.js`**:
  * 4 summary metric cards (Total Reports, Pages Indexed, Words, Emails).
  * Domain card grid with search, sorting, and delete button.
  * Slide-in detail drawer with tabs: Summary, Pages (with "Load More" pagination), Contacts (1-click clipboard copy), and SEO schemas.
  * Download center for Markdown, JSON, and CSV.
* **`frontend/schedules.html` & `frontend/schedules.js`**:
  * Summary filter pills (All, Active, Paused).
  * Schedule table with frequency badge, active/paused switch, next run, last run, and actions.
  * Modal for creating and editing schedules with customizable crawl scope limits.
* **`frontend/settings.html` & `frontend/settings.js`**:
  * 4 tabs: Crawler Defaults, Pipeline Stage toggles, Storage & Maintenance, and Appearance & UX.
  * Storage breakdown in MB and "Clear Checkpoint" utility.
  * Password mask/unmask toggle for Jina API key.
* **`frontend/index.html` & `frontend/crawls.html`**:
  * Synchronized all 5 sidebar navigation links.
  * Connected dashboard "View Analytics" button directly to `/reports`.
* **`frontend/styles.css`**: Added styling for global toast notifications.
