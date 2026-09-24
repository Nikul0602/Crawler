# CrawlMaster — Remaining Work by Environment

## Development Environment

These items are required to finish implementation and verify the application locally:

- Install dependencies from `requirements.txt`, including FastAPI, Uvicorn, `croniter`, `ijson`, pytest, and pytest-asyncio.
- Run Python compilation and the complete pytest suite.
- Add automated tests for:
  - cancellation and worker shutdown;
  - schedule deletion while a crawl is active;
  - report publication and cleanup after cancellation;
  - task-specific checkpoint isolation;
  - schedule-claim race prevention;
  - API status/error contracts;
  - settings precedence and validation.
- Complete the logs/details drawer and More Options menu in the Crawls page.
- Replace unsafe frontend `innerHTML` rendering with escaped or DOM-based rendering.
- Add local smoke tests for Reports, Schedules, Settings, and Cancel Crawl flows.
- Test cancellation against mocked slow network/browser stages.

Development completion criteria:

```text
pip install -r requirements.txt
python -m compileall -q backend tests
pytest tests/ -v
python main.py --mode server
```

## Production Environment

These items are required before exposing the dashboard to users or the network:

- Add redirect validation so every redirect destination is checked for unsafe schemes, private IPs, loopback, link-local, reserved, and metadata addresses.
- Protect against DNS rebinding by resolving and validating destinations immediately before each request, not only during initial URL validation.
- Apply cancellation and bounded timeouts inside every third-party network/browser stage.
- Ensure browser, HTTP, and thread resources are closed after cancellation, failure, shutdown, and timeout.
- Add authentication and authorization for crawl, delete, settings, schedule, API-key, and maintenance endpoints.
- Use HTTPS and restrict CORS when the service is exposed beyond localhost.
- Add structured logging, task correlation IDs, health/readiness endpoints, and worker monitoring.
- Define resource limits for response size, crawl duration, concurrency, disk usage, redirects, and scheduled jobs.
- Add retention and cleanup policies for reports, checkpoints, logs, and temporary directories.
- Verify that secrets never appear in API responses, logs, reports, or error messages.
- Run security tests for SSRF, path traversal, symlink escapes, XSS, CSV injection, and unauthorized destructive actions.

Production completion criteria:

```text
No unsafe redirect or DNS-rebinding request succeeds.
Cancelled crawls terminate within the configured grace period.
All active resources are released after cancellation or shutdown.
Unauthenticated users cannot invoke destructive or secret-bearing APIs.
Monitoring can identify active, failed, cancelled, and stuck workers.
```

## Priority

1. Install dependencies and run the automated tests.
2. Complete cancellation and worker-shutdown tests.
3. Finish frontend logs/options and XSS-safe rendering.
4. Add redirect/DNS-rebinding protection.
5. Add production authentication, resource limits, and observability.
