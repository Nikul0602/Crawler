"""Report manager — reads the output/ directory and serves report data.

Key design decisions (from implementation plan):
- Summary listing uses summary.json (fast path) or reads only top-level
  scalar fields from data.json without loading the pages array.
- Path traversal protection: domain regex + is_relative_to() guard.
- Deletion is blocked while a task with that domain is Running.
- Pagination via get_pages() keeps detail responses bounded.
"""

import csv
import io
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from backend.paths import OUTPUT_DIR
from backend.services.storage import write_json_atomic

try:
    import ijson
except ImportError:  # compatibility for environments before dependency install
    ijson = None

# Domain names: allow letters, digits, dots, hyphens, underscores
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

# Top-level scalar fields we extract for summary (no pages array needed)
_SUMMARY_FIELDS = (
    "domain", "base_url", "site_title", "platform", "language",
    "crawl_started", "crawl_finished", "crawl_stopped_reason",
    "total_urls_found", "total_pages_crawled", "failed_pages",
    "total_words", "total_images",
)

# Simple in-process cache: maps (domain, mtime) → summary dict
_summary_cache: Dict[str, Any] = {}


class ReportManager:

    # ── Validation helpers ────────────────────────────────────────────────────

    def _validate_domain(self, domain: str) -> Optional[Path]:
        """Return the resolved report directory, or None if invalid/unsafe."""
        if not _DOMAIN_RE.match(domain):
            return None
        candidate = (OUTPUT_DIR / domain).resolve()
        try:
            candidate.relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            return None
        if not candidate.is_dir():
            return None
        return candidate

    def get_report_dir(self, domain: str) -> Optional[Path]:
        """Return a validated report directory for safe file serving."""
        return self._validate_domain(domain)

    # ── Summary helpers ───────────────────────────────────────────────────────

    def _read_summary_fast(self, report_dir: Path) -> Optional[Dict[str, Any]]:
        """Try reading the pre-generated summary.json (fast path)."""
        summary_file = report_dir / "summary.json"
        if summary_file.exists():
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _read_summary_compat(self, report_dir: Path) -> Dict[str, Any]:
        """Compatibility fallback: read only scalar fields from data.json.

        Streams through data.json looking for the top-level scalar keys without
        loading the entire 'pages' array into memory. Uses a cache keyed by
        (domain, mtime) to avoid repeated parses.
        """
        data_file = report_dir / "data.json"
        domain = report_dir.name
        mtime = data_file.stat().st_mtime if data_file.exists() else 0
        cache_key = f"{domain}:{mtime}"

        if cache_key in _summary_cache:
            return _summary_cache[cache_key]

        summary: Dict[str, Any] = {"domain": domain}
        if data_file.exists():
            try:
                if ijson is not None:
                    with open(data_file, "rb") as f:
                        for key, value in ijson.kvitems(f, ""):
                            if key in _SUMMARY_FIELDS:
                                summary[key] = value
                            elif key == "all_emails":
                                summary["email_count"] = len(value)
                            elif key == "all_phones":
                                summary["phone_count"] = len(value)
                else:
                    with open(data_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for field in _SUMMARY_FIELDS:
                        if field in data:
                            summary[field] = data[field]
                    summary["email_count"] = len(data.get("all_emails", []))
                    summary["phone_count"] = len(data.get("all_phones", []))
            except Exception:
                pass

        _summary_cache[cache_key] = summary
        return summary

    # ── Public API ────────────────────────────────────────────────────────────

    def get_all_summaries(self) -> List[Dict[str, Any]]:
        """Return a lightweight summary for every domain in OUTPUT_DIR."""
        results = []
        if not OUTPUT_DIR.exists():
            return results

        for report_dir in sorted(OUTPUT_DIR.iterdir()):
            if not report_dir.is_dir():
                continue
            domain = report_dir.name
            if not _DOMAIN_RE.match(domain):
                continue

            # Skip temporary crawl directories (e.g. _tmp_*)
            if domain.startswith("_"):
                continue

            summary = self._read_summary_fast(report_dir)
            if summary is None:
                summary = self._read_summary_compat(report_dir)

            results.append(summary)

        # Sort by crawl_finished descending
        results.sort(key=lambda s: s.get("crawl_finished", ""), reverse=True)
        return results

    def get_report_detail(self, domain: str) -> Optional[Dict[str, Any]]:
        """Return report metadata + first 50 pages."""
        report_dir = self._validate_domain(domain)
        if report_dir is None:
            return None

        data_file = report_dir / "data.json"
        if not data_file.exists():
            return None

        try:
            result: Dict[str, Any] = {}
            first_pages: list[Any] = []
            total_pages = 0
            if ijson is not None:
                with open(data_file, "rb") as f:
                    for key, value in ijson.kvitems(f, ""):
                        if key != "pages":
                            result[key] = value
                with open(data_file, "rb") as f:
                    for page in ijson.items(f, "pages.item"):
                        total_pages += 1
                        if len(first_pages) < 50:
                            first_pages.append(page)
            else:
                with open(data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                first_pages = data.get("pages", [])[:50]
                total_pages = len(data.get("pages", []))
            result["pages"] = first_pages
            result["total_page_count"] = total_pages
        except Exception:
            return None
        return result

    def get_pages(
        self,
        domain: str,
        offset: int = 0,
        limit: int = 25,
        query: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Return a paginated slice of pages for the given domain."""
        report_dir = self._validate_domain(domain)
        if report_dir is None:
            return None

        data_file = report_dir / "data.json"
        if not data_file.exists():
            return None

        try:
            limit = max(1, min(limit, 100))
            offset = max(0, offset)
            page_slice: List[Any] = []
            total = 0
            iterator = None
            if ijson is not None:
                with open(data_file, "rb") as f:
                    iterator = ijson.items(f, "pages.item")
                    for page in iterator:
                        if query and query.lower() not in page.get("url", "").lower() and query.lower() not in page.get("page_title", "").lower():
                            continue
                        if total >= offset and len(page_slice) < limit:
                            page_slice.append(page)
                        total += 1
            else:
                with open(data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pages = data.get("pages", [])
                if query:
                    q = query.lower()
                    pages = [p for p in pages if q in p.get("url", "").lower() or q in p.get("page_title", "").lower()]
                total = len(pages)
                page_slice = pages[offset:offset + limit]
        except Exception:
            return None

        return {
            "domain": domain,
            "total": len(pages),
            "offset": offset,
            "limit": limit,
            "pages": page_slice,
        }

    def delete_report(self, domain: str, active_domains: Set[str]) -> bool:
        """Delete a report directory.

        Returns False if the domain is actively being crawled or is invalid.
        """
        if domain in active_domains:
            return False
        report_dir = self._validate_domain(domain)
        if report_dir is None:
            return False
        try:
            shutil.rmtree(report_dir)
            # Clear cache entries for this domain
            keys_to_del = [k for k in _summary_cache if k.startswith(f"{domain}:")]
            for k in keys_to_del:
                del _summary_cache[k]
            return True
        except Exception:
            return False

    def generate_summary_json(self, domain: str, data_json_path: Path, report: Any = None) -> None:
        """Write a summary.json beside data.json after a completed crawl."""
        try:
            if report is not None:
                data = {field: getattr(report, field, None) for field in _SUMMARY_FIELDS}
                data["email_count"] = len(getattr(report, "all_emails", []))
                data["phone_count"] = len(getattr(report, "all_phones", []))
            else:
                with open(data_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

            summary: Dict[str, Any] = {}
            for field in _SUMMARY_FIELDS:
                if field in data:
                    summary[field] = data[field]
            summary["email_count"] = len(data.get("all_emails", []))
            summary["phone_count"] = len(data.get("all_phones", []))

            summary_path = data_json_path.parent / "summary.json"
            write_json_atomic(summary_path, summary)
        except Exception:
            pass  # non-fatal — listing will fall back to compat mode

    def stream_csv(self, domain: str) -> Optional[Iterator[str]]:
        """Yield CSV rows for all pages of the given domain."""
        report_dir = self._validate_domain(domain)
        if report_dir is None:
            return None

        data_file = report_dir / "data.json"
        if not data_file.exists():
            return None

        def _generate():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "url", "page_title", "depth", "word_count",
                "emails", "phones", "images_count",
            ])
            yield output.getvalue()
            output.truncate(0)
            output.seek(0)

            try:
                if ijson is not None:
                    with open(data_file, "rb") as f:
                        pages = ijson.items(f, "pages.item")
                        for page in pages:
                            yield from _csv_page(page, writer, output)
                else:
                    with open(data_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for page in data.get("pages", []):
                        yield from _csv_page(page, writer, output)
            except Exception:
                return

        def _csv_page(page: Dict[str, Any], writer, output):
                # Prefix dangerous formula cells with apostrophe
                def safe(val: str) -> str:
                    val = str(val)
                    if val.startswith(("=", "+", "-", "@")):
                        return "'" + val
                    return val

                writer.writerow([
                    safe(page.get("url", "")),
                    safe(page.get("page_title", "")),
                    page.get("depth", 0),
                    page.get("word_count", 0),
                    "; ".join(page.get("emails", [])),
                    "; ".join(page.get("phones", [])),
                    len(page.get("images", [])),
                ])
                yield output.getvalue()
                output.truncate(0)
                output.seek(0)

        return _generate()


report_manager = ReportManager()
