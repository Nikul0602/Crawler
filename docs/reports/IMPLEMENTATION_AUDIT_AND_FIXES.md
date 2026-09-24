# CrawlMaster Implementation Audit, Issues, and Fixes

## Audit Scope

This report compares the current implementation with [`implementation_plan.md`](../plans/implementation_plan.md) and [`IMPLEMENTATION_SUMMARY.md`](IMPLEMENTATION_SUMMARY.md).

The backend passes `python -m compileall -q backend`. No automated test files currently exist, and endpoint-level smoke testing requires the project dependencies to be installed. Therefore, implemented items below are confirmed by source inspection, not complete runtime verification.

## Executive Summary

Reports, schedules, settings, task APIs, atomic JSON storage, and a cooperative crawl-cancellation foundation are present. Several features remain incomplete or incorrectly implemented.

The original critical issue was that deleting a schedule or cancelling a crawl did not stop the already-running crawler. A cancellation path now exists, but cancellation is cooperative: a third-party request already blocked in a browser or network library may continue until its timeout. The system must not claim a worker has fully stopped until the underlying crawl has exited.

## Status Overview

| Area | Status | Assessment |
|---|---|---|
| Shared paths and directories | Implemented | Data, schedule, and settings paths exist. |
| Atomic JSON writes | Mostly implemented | Safe for same-process threads, not a multi-process transaction. |
| Settings persistence | Partially implemented | Persistence and masking work; strict validation and reproducible precedence remain incomplete. |
| Report list/detail APIs | Partially implemented | Routes exist, but several reads still load complete JSON files. |
| Report download validation | Implemented | Domain and format validation now occur before serving files. |
| Atomic report publication | Not implemented | Reports are still written directly into the live domain directory. |
| Schedule CRUD | Implemented | Create, update, delete, toggle, and run-now routes exist. |
| Scheduled worker dispatch | Implemented | Workers are scheduled through `create_task` and `to_thread`. |
| Schedule claim atomicity | Partially implemented | Claim is locked, but due-check and claim remain separate. |
| Task cancellation API | Implemented | `POST /api/tasks/{task_id}/cancel` exists. |
| Cooperative crawler cancellation | Partially implemented | Orchestrator checks cancellation, but network stages are not fully cancellable. |
| Schedule deletion cancellation | Partially implemented | Active tasks can be signalled, but deletion may return before cancellation finishes. |
| Worker shutdown | Partially implemented | Workers are tracked and signalled; Python threads cannot be forcibly killed. |
| Task-specific checkpoints | Not implemented | All crawls still use root `crawl_state.json`. |
| Crawls table actions | Incomplete | Cancel exists; logs and more-options actions remain disabled. |
| Frontend safety | Incomplete | Crawler-derived values are still inserted through `innerHTML`. |
| Automated tests | Missing | No test suite currently exists. |

## Correctly Implemented Features

### Shared storage

The project contains `DATA_DIR`, `TASKS_FILE`, `SCHEDULES_FILE`, and `SETTINGS_FILE`. JSON writes use unique temporary filenames, `fsync`, `os.replace`, and in-process per-file locks. This protects against partial writes and same-process thread collisions, but does not provide a durable multi-process lock.

### Settings persistence and secret masking

Settings persist in `data/settings.json`. The Jina API key is not returned raw, and a masked placeholder can be submitted without replacing the stored key.

### Report APIs

The following routes exist:

- `GET /api/reports`
- `GET /api/reports/{domain}`
- `GET /api/reports/{domain}/pages`
- `GET /api/reports/{domain}/download`
- `DELETE /api/reports/{domain}`

Report domain validation is now applied to downloads as well as detail and deletion operations.

### Schedule and task APIs

Schedule CRUD, toggling, run-now, task detail, re-run, delete, and cancellation routes exist. Tasks store `request_config`, `schedule_id`, `target_domain`, `report_domain`, and cancellation timestamps.

### Cancellation foundation

The implementation includes:

- A per-task cancellation-event registry.
- A worker registry.
- `CancellationRequested` and `Cancelled` states.
- Orchestrator checks before queue/page work.
- Cancellation signalling during schedule deletion.
- Cancellation signalling during application shutdown.
- A cancel action on the Crawls page.

## Incomplete or Incorrect Features

### 1. Cancellation does not interrupt every active operation

The event is checked by the orchestrator, but it is not passed into every pipeline stage or third-party browser/network operation. A request already blocked in a library may continue until its timeout.

#### Fix

- Set connect, read, and total timeouts for every HTTP operation.
- Use cancellation-aware async waits where possible.
- Cancel pending asyncio page tasks when the event is set.
- Close browser and HTTP clients in `finally` blocks.
- Use a bounded cancellation grace period.
- Mark a task `CancellationTimedOut` or `Interrupted` if the worker does not exit in time.

### 2. A cancelled task can be reported before the worker thread exits

Python cannot forcibly terminate a thread running blocking code.

#### Fix

Keep the task visible until the worker exits. Return `202 Accepted` while cancellation is pending. Only expose final `Cancelled` after cleanup has completed.

### 3. Schedule deletion can finish before its crawl stops

`DELETE /api/schedules/{id}?cancel_active=true` signals active tasks and deletes the schedule while the crawl may still be running.

#### Fix

Return `202` with `cancellation_pending: true`, or wait for bounded graceful shutdown before returning success. Keep the schedule ID on the task so final status remains traceable.

### 4. Schedule claim and due-check are separate

`get_due_schedules()` and `claim_schedule()` can race with another scheduler tick or `Run Now`.

#### Fix

Add a locked `claim_due_schedule(schedule_id, task_id)` operation that re-checks eligibility, advances `next_run_at`, records the claim, and persists everything in one critical section.

### 5. Cancellation status updates are not centralized

Normal completion and failure update the associated schedule, but early cancellation and exception cancellation can leave a schedule marked `Running`.

#### Fix

Use centralized finalizers for completed, failed, cancelled, and timed-out tasks. Each finalizer must update task status, schedule status, timestamps, and worker cleanup.

### 6. Checkpoints are shared by all crawls

All crawls still use root-level `crawl_state.json`. Concurrent crawls can overwrite one another’s resume state.

#### Fix

Use `data/checkpoints/{task_id}.json`. Pass that path into `CrawlerConfig` and `CrawlOrchestrator`, and clear only the selected task checkpoint.

### 7. Report publication is not atomic

`data.json`, `report.md`, and `summary.json` are still written directly into `output/{domain}`.

#### Fix

Write all files into `output/.tmp/{task_id}`, validate them, acquire a per-domain lock, and atomically promote the completed directory. Cancelled crawls should discard temporary output by default.

### 8. Report readers are not fully memory bounded

The compatibility summary reader, detail route, and page route still use `json.load()` on complete report files.

#### Fix

Generate `summary.json` directly from the in-memory report, use a streaming parser or one-time migration for legacy reports, and create a page index for bounded pagination.

### 9. CSV export is not truly streaming

The CSV generator yields rows incrementally but loads the complete report before yielding.

#### Fix

Use a streaming parser or generate a page index during report publication.

### 10. Settings precedence is not fully reproducible

Missing request values can use saved settings, but a task may store `None` values. A re-run can therefore use newer settings instead of the original effective settings.

#### Fix

Resolve the complete effective configuration when creating the task and store that snapshot. Re-runs must use the snapshot, excluding or securely referencing secrets.

### 11. Settings validation is permissive

Invalid values are silently skipped or coerced. For example, string booleans can be interpreted incorrectly.

#### Fix

Use strict Pydantic models with bounds and explicit `422` errors. Validate settings loaded from disk as well as API input.

### 12. Custom cron support has an inaccurate fallback

`croniter` is listed in `requirements.txt`, but unavailable or invalid expressions can still fall back to approximate daily behavior.

#### Fix

Require `croniter` for custom schedules, validate expressions during create/update, and return a clear validation error instead of silently falling back.

### 13. Crawls table actions remain incomplete

The page still renders `View Logs (coming soon)` and `More Options (coming soon)`.

#### Fix

Implement or remove these controls. Required actions are task detail/log drawer, view report, cancel, re-run, and delete completed task.

### 14. Frontend injection risk remains

Crawler URLs, errors, titles, and contact values are interpolated through `innerHTML`.

#### Fix

Use `textContent` for untrusted text, escape unavoidable templates, restrict link protocols to HTTP/HTTPS, and add XSS fixtures.

### 15. SSRF and resource-abuse protections are missing

Manual and scheduled URLs can target local services, private IPs, metadata endpoints, unsupported schemes, or unsafe redirects.

#### Fix

Allow only HTTP/HTTPS, block loopback/private/link-local/reserved destinations by default, re-check redirects, and enforce response-size, redirect-count, concurrency, disk, and total-time limits.

### 16. Storage metrics may follow symlinks

Recursive storage measurement can count files outside the intended directory through symlinks.

#### Fix

Reject symlinks or resolve each file and verify containment before counting it.

### 17. Error contracts are inconsistent

Some endpoints return proper HTTP errors while `/api/status/{task_id}` can return an error object with HTTP 200.

#### Fix

Use consistent `400`, `404`, `409`, `422`, and `500` responses. Add a shared frontend fetch helper that checks `response.ok`.

### 18. No automated tests exist

There are no tests covering the new architecture.

#### Fix

Add tests for cancellation, schedule deletion, shutdown, duplicate claims, traversal, symlink escapes, atomic publication, task checkpoints, settings precedence, malformed JSON, CSV injection, frontend escaping, and same-domain concurrent crawls.

## Required Cancellation Lifecycle

```text
User clicks Cancel or Delete Schedule
        |
        v
API marks task CancellationRequested
        |
        v
Cancellation registry sets task Event
        |
        v
Crawler stops queue expansion and page scheduling
        |
        v
Pending page tasks are cancelled
        |
        v
Active requests finish or hit bounded timeout
        |
        v
Browser/HTTP resources are closed
        |
        v
Temporary output is discarded
        |
        v
Task becomes Cancelled
        |
        v
Worker registry removes task
```

The key invariant is:

> A task must not be considered fully cancelled or deleted until its worker has stopped, or the API must explicitly report that cancellation is pending.

## Recommended Implementation Order

1. Centralize task finalization for completed, failed, cancelled, and timed-out tasks.
2. Add bounded cancellation to every network and browser stage.
3. Make schedule claiming transactional.
4. Make schedule deletion cancellation-aware with accurate `202`/`409` responses.
5. Move checkpoints to task-specific files.
6. Implement atomic temporary report publication.
7. Resolve and persist complete effective crawl configurations.
8. Enforce strict settings, schedule, and URL validation.
9. Complete the Crawls table actions.
10. Remove frontend `innerHTML` injection risks.
11. Add automated cancellation, recovery, security, and concurrency tests.

## Acceptance Criteria

The implementation is complete only when:

- Cancel changes the task to `CancellationRequested` immediately.
- The crawler stops discovering and fetching new pages.
- Active requests finish or timeout within the configured grace period.
- Browser and HTTP resources are closed.
- The worker exits and is removed from the worker registry.
- The final task state is `Cancelled`.
- Deleting a schedule cannot leave its active crawl running indefinitely.
- Schedule deletion accurately reports pending cancellation.
- A cancelled crawl cannot overwrite a completed report.
- Server shutdown requests cancellation and waits for workers.
- A cancelled task does not reappear as `Running` after restart.
- Automated tests prove no active worker remains after cancellation.
