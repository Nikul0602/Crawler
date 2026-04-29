"""
IT Job extraction logic — parses crawled content to find IT/tech positions.
"""

import re
import logging
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from backend.core.models import JobListing, CrawlResponse
from backend.core.config import PipelineConfig

logger = logging.getLogger(__name__)


class ITJobExtractor:
    """Extracts IT job listings from crawled career page content."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.it_keywords = [kw.lower() for kw in self.config.it_keywords]
        self.dept_keywords = [kw.lower() for kw in self.config.it_department_keywords]

    def extract(self, response: CrawlResponse) -> list[JobListing]:
        """
        Extract IT job listings from a CrawlResponse.
        
        Tries multiple extraction strategies:
        1. Structured HTML parsing (look for job cards/links)
        2. Markdown/text line-by-line parsing
        """
        if not response.success or not response.content:
            logger.warning("No content to extract jobs from")
            return []

        jobs: list[JobListing] = []

        # Strategy 1: Parse structured HTML if available
        if response.html:
            html_jobs = self._extract_from_html(response.html, response.url)
            jobs.extend(html_jobs)
            logger.info(f"HTML extraction found {len(html_jobs)} potential jobs")

        # Strategy 2: Parse from text/markdown (catches things HTML parsing might miss)
        if response.content and not jobs:
            text_jobs = self._extract_from_text(response.content, response.url)
            jobs.extend(text_jobs)
            logger.info(f"Text extraction found {len(text_jobs)} potential jobs")

        # Deduplicate by title
        jobs = self._deduplicate(jobs)

        # Filter to IT roles only
        it_jobs = [job for job in jobs if self._is_it_job(job)]

        logger.info(
            f"📊 Extraction complete: {len(jobs)} total jobs → {len(it_jobs)} IT jobs"
        )

        return it_jobs

    def _extract_from_html(self, html: str, source_url: str) -> list[JobListing]:
        """Extract jobs by parsing HTML structure."""
        soup = BeautifulSoup(html, "lxml")
        jobs = []

        # ──── Strategy A: Common career page patterns ────

        # Pattern 1: Look for job listing containers
        # Many career pages use these class patterns
        job_selectors = [
            '[class*="job-listing"]',
            '[class*="job-card"]',
            '[class*="job-item"]',
            '[class*="position-card"]',
            '[class*="career-item"]',
            '[class*="opening"]',
            '[class*="vacancy"]',
            '[data-job]',
            '[data-position]',
            'tr[class*="job"]',
            'li[class*="job"]',
            '.posting-title',           # Lever
            '.job-result',              # Custom
            '[class*="JobCard"]',       # React-style
            '[class*="jobCard"]',
            '[class*="requisition"]',   # Workday
        ]

        for selector in job_selectors:
            elements = soup.select(selector)
            for el in elements:
                job = self._parse_job_element(el, source_url)
                if job and job.title:
                    jobs.append(job)

        # Pattern 2: Look for links to job detail pages
        if not jobs:
            link_patterns = [
                r'/job[s]?/',
                r'/position[s]?/',
                r'/career[s]?/',
                r'/opening[s]?/',
                r'/role[s]?/',
                r'/requisition/',
                r'lever\.co/',
                r'greenhouse\.io/',
                r'workday\.com/',
                r'ashbyhq\.com/',
                r'smartrecruiters\.com/',
            ]

            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                link_text = link.get_text(strip=True)

                if any(re.search(pat, href, re.I) for pat in link_patterns):
                    if link_text and len(link_text) > 3 and len(link_text) < 200:
                        full_url = urljoin(source_url, href)
                        job = JobListing(
                            title=link_text,
                            url=full_url,
                            source_url=source_url,
                        )
                        # Try to get location/department from nearby elements
                        self._enrich_from_context(job, link)
                        jobs.append(job)

        return jobs

    def _parse_job_element(self, element, source_url: str) -> Optional[JobListing]:
        """Parse a single job element/card into a JobListing."""
        # Try to find the title
        title_el = (
            element.select_one('h1, h2, h3, h4, .title, [class*="title"], [class*="Title"]')
            or element.select_one('a')
        )

        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        if not title or len(title) < 3:
            return None

        # Get URL
        link = element.select_one('a[href]')
        url = ""
        if link:
            url = urljoin(source_url, link.get('href', ''))

        # Get location
        location = ""
        loc_el = element.select_one(
            '[class*="location"], [class*="Location"], '
            '[class*="place"], [class*="city"]'
        )
        if loc_el:
            location = loc_el.get_text(strip=True)

        # Get department
        department = ""
        dept_el = element.select_one(
            '[class*="department"], [class*="Department"], '
            '[class*="team"], [class*="Team"], [class*="category"]'
        )
        if dept_el:
            department = dept_el.get_text(strip=True)

        # Get job type
        job_type = ""
        type_el = element.select_one(
            '[class*="type"], [class*="Type"], '
            '[class*="employment"], [class*="commitment"]'
        )
        if type_el:
            job_type = type_el.get_text(strip=True)

        return JobListing(
            title=title,
            location=location,
            department=department,
            job_type=job_type,
            url=url,
            source_url=source_url,
        )

    def _enrich_from_context(self, job: JobListing, link_element) -> None:
        """Try to extract location/dept from surrounding elements."""
        parent = link_element.parent
        if parent:
            text = parent.get_text(separator=" | ", strip=True)
            # Simple heuristic: if there are parts separated by |, -, or •
            parts = re.split(r'[|•–—-]', text)
            if len(parts) >= 2:
                # First part is likely the title (already have it)
                for part in parts[1:]:
                    part = part.strip()
                    if any(loc in part.lower() for loc in [
                        'remote', 'hybrid', 'onsite', 'office',
                        'new york', 'san francisco', 'london', 'berlin',
                        'bangalore', 'india', 'usa', 'uk', 'canada',
                    ]):
                        job.location = part
                    elif any(dept in part.lower() for dept in self.dept_keywords):
                        job.department = part

    def _extract_from_text(self, text: str, source_url: str) -> list[JobListing]:
        """
        Extract jobs from plain text/markdown content.
        Looks for lines that appear to be job titles.
        """
        jobs = []
        lines = text.split('\n')

        for i, line in enumerate(lines):
            line = line.strip()

            # Skip empty, too short, or too long lines
            if not line or len(line) < 5 or len(line) > 200:
                continue

            # Remove markdown headers
            clean = re.sub(r'^#{1,6}\s*', '', line)
            # Remove markdown links but keep text
            clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean)
            clean = clean.strip()

            if not clean:
                continue

            # Check if this line looks like a job title
            if self._looks_like_job_title(clean):
                # Look at surrounding lines for context
                location = ""
                department = ""
                for j in range(i + 1, min(i + 4, len(lines))):
                    context_line = lines[j].strip()
                    if any(loc in context_line.lower() for loc in [
                        'remote', 'hybrid', 'location', 'office'
                    ]):
                        location = context_line
                    elif any(dept in context_line.lower() for dept in self.dept_keywords):
                        department = context_line

                # Extract URL from markdown link if present
                url_match = re.search(r'\[.*?\]\((https?://[^\)]+)\)', line)
                url = url_match.group(1) if url_match else ""

                jobs.append(JobListing(
                    title=clean,
                    location=location,
                    department=department,
                    url=url,
                    source_url=source_url,
                ))

        return jobs

    def _looks_like_job_title(self, text: str) -> bool:
        """Heuristic check if a text line looks like a job title."""
        text_lower = text.lower()

        # Must contain at least one job-related keyword
        job_title_indicators = [
            'engineer', 'developer', 'manager', 'analyst', 'architect',
            'designer', 'administrator', 'specialist', 'consultant',
            'lead', 'director', 'coordinator', 'scientist', 'intern',
            'associate', 'officer', 'head of', 'vp of', 'chief',
        ]

        has_title_word = any(ind in text_lower for ind in job_title_indicators)

        # Should not be a sentence (job titles are short phrases)
        is_short = len(text.split()) <= 12
        no_period = '.' not in text or text.endswith('.')

        return has_title_word and is_short

    def _is_it_job(self, job: JobListing) -> bool:
        """
        Determine if a job listing is an IT/tech role.
        Checks title AND department against IT keywords.
        """
        searchable = f"{job.title} {job.department} {job.description}".lower()

        for keyword in self.it_keywords:
            if keyword in searchable:
                job.is_it_role = True
                return True

        # Also check department keywords
        if job.department:
            dept_lower = job.department.lower()
            if any(dk in dept_lower for dk in self.dept_keywords):
                # Department matches, but also verify title isn't clearly non-IT
                non_it_indicators = [
                    'accountant', 'recruiter', 'hr ', 'human resource',
                    'marketing', 'sales rep', 'legal', 'lawyer',
                    'nurse', 'doctor', 'receptionist', 'janitor',
                ]
                if not any(ni in searchable for ni in non_it_indicators):
                    job.is_it_role = True
                    return True

        return False

    def _deduplicate(self, jobs: list[JobListing]) -> list[JobListing]:
        """Remove duplicate jobs based on title similarity."""
        seen_titles = set()
        unique = []

        for job in jobs:
            # Normalize title for comparison
            normalized = re.sub(r'[^a-z0-9\s]', '', job.title.lower()).strip()
            if normalized and normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(job)

        return unique
