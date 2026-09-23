// Reports Page JavaScript
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('visible'), 10);
    setTimeout(() => {
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

document.addEventListener('DOMContentLoaded', () => {
    // Theme toggle
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
            document.documentElement.dataset.theme = next;
            localStorage.setItem('crawlmaster-theme', next);
        });
    }

    // State
    let allReports = [];
    let currentDomain = null;
    let currentPagesOffset = 0;
    const PAGE_LIMIT = 25;

    // Elements
    const grid = document.getElementById('reports-grid');
    const searchInput = document.getElementById('report-search');
    const sortSelect = document.getElementById('report-sort');
    const drawerOverlay = document.getElementById('drawer-overlay');
    const drawerClose = document.getElementById('drawer-close');

    // Drawer elements
    const drawerDomain = document.getElementById('drawer-domain');
    const drawerBaseUrl = document.getElementById('drawer-base-url');
    const sumPages = document.getElementById('sum-pages');
    const sumWords = document.getElementById('sum-words');
    const sumImages = document.getElementById('sum-images');
    const sumReason = document.getElementById('sum-reason');
    const sumTiming = document.getElementById('sum-timing');
    const pagesList = document.getElementById('pages-list');
    const pagesQuery = document.getElementById('pages-query');
    const btnLoadMore = document.getElementById('btn-load-more');
    const contactsEmails = document.getElementById('contacts-emails');
    const contactsPhones = document.getElementById('contacts-phones');
    const contactsSocial = document.getElementById('contacts-social');
    const seoList = document.getElementById('seo-list');
    const tabCount = document.getElementById('tab-page-count');
    const btnMd = document.getElementById('btn-download-md');
    const btnCsv = document.getElementById('btn-download-csv');
    const btnJson = document.getElementById('btn-download-json');

    // Stats
    const statReports = document.getElementById('stat-total-reports');
    const statPages = document.getElementById('stat-total-pages');
    const statWords = document.getElementById('stat-total-words');
    const statEmails = document.getElementById('stat-total-emails');

    // Drawer Tabs
    document.querySelectorAll('.drawer-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.drawer-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.drawer-pane').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const target = document.getElementById(`pane-${tab.dataset.tab}`);
            if (target) target.classList.add('active');
        });
    });

    // Close drawer
    drawerClose.addEventListener('click', () => drawerOverlay.classList.remove('active'));
    drawerOverlay.addEventListener('click', (e) => {
        if (e.target === drawerOverlay) drawerOverlay.classList.remove('active');
    });

    // Fetch reports
    async function loadReports() {
        try {
            const res = await fetch('/api/reports');
            allReports = await res.json();
            updateStats();
            renderReports();
        } catch (e) {
            showToast('Failed to load reports', 'error');
        }
    }

    function updateStats() {
        statReports.textContent = allReports.length;
        let pages = 0;
        let words = 0;
        let emails = 0;
        allReports.forEach(r => {
            pages += (r.total_pages_crawled || 0);
            words += (r.total_words || 0);
            emails += (r.email_count || 0);
        });
        statPages.textContent = pages > 1000 ? (pages / 1000).toFixed(1) + 'K' : pages;
        statWords.textContent = words > 1000000 ? (words / 1000000).toFixed(1) + 'M' : (words > 1000 ? (words / 1000).toFixed(0) + 'K' : words);
        statEmails.textContent = emails;
    }

    function renderReports() {
        grid.innerHTML = '';
        const q = searchInput.value.toLowerCase().trim();
        const sort = sortSelect.value;

        let filtered = allReports.filter(r => {
            const domain = (r.domain || '').toLowerCase();
            const title = (r.site_title || '').toLowerCase();
            return domain.includes(q) || title.includes(q);
        });

        filtered.sort((a, b) => {
            if (sort === 'oldest') return (a.crawl_finished || '').localeCompare(b.crawl_finished || '');
            if (sort === 'pages-desc') return (b.total_pages_crawled || 0) - (a.total_pages_crawled || 0);
            if (sort === 'words-desc') return (b.total_words || 0) - (a.total_words || 0);
            return (b.crawl_finished || '').localeCompare(a.crawl_finished || '');
        });

        if (filtered.length === 0) {
            grid.innerHTML = `<div class="empty-placeholder" style="grid-column: 1/-1;">No reports found. Completed crawls will appear here.</div>`;
            return;
        }

        filtered.forEach(r => {
            const card = document.createElement('div');
            card.className = 'report-card';

            const domain = r.domain || 'unknown';
            const initial = domain.charAt(0).toUpperCase();
            const dateStr = r.crawl_finished ? new Date(r.crawl_finished).toLocaleDateString() : 'Unknown';

            card.innerHTML = `
                <div class="report-card-header">
                    <div class="report-avatar">${initial}</div>
                    <div class="report-title-box">
                        <div class="report-domain" title="${domain}">${domain}</div>
                        <div class="report-url">${r.base_url || ''}</div>
                    </div>
                    <button class="btn-card-delete" title="Delete report">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                </div>
                <div class="report-metrics">
                    <div class="metric-item"><span class="metric-label">Pages</span><span class="metric-value">${r.total_pages_crawled || 0}</span></div>
                    <div class="metric-item"><span class="metric-label">Words</span><span class="metric-value">${((r.total_words || 0) > 1000 ? ((r.total_words || 0)/1000).toFixed(1) + 'k' : (r.total_words || 0))}</span></div>
                    <div class="metric-item"><span class="metric-label">Images</span><span class="metric-value">${r.total_images || 0}</span></div>
                    <div class="metric-item"><span class="metric-label">Emails</span><span class="metric-value">${r.email_count || 0}</span></div>
                </div>
                <div class="report-footer">
                    <span class="report-date">${dateStr}</span>
                    <div class="report-actions">
                        <button class="btn btn-primary btn-view" style="padding: 6px 12px; font-size: 13px;">View</button>
                    </div>
                </div>
            `;

            card.querySelector('.btn-view').addEventListener('click', () => openReportDetail(domain));
            card.querySelector('.btn-card-delete').addEventListener('click', (e) => {
                e.stopPropagation();
                if (confirm(`Are you sure you want to delete the report for "${domain}"?`)) {
                    deleteReport(domain);
                }
            });

            grid.appendChild(card);
        });
    }

    async function deleteReport(domain) {
        try {
            const res = await fetch(`/api/reports/${encodeURIComponent(domain)}`, { method: 'DELETE' });
            if (res.ok) {
                showToast(`Report ${domain} deleted`, 'success');
                loadReports();
            } else {
                const err = await res.json();
                showToast(err.detail || 'Delete failed', 'error');
            }
        } catch (e) {
            showToast('Network error while deleting', 'error');
        }
    }

    async function openReportDetail(domain) {
        currentDomain = domain;
        currentPagesOffset = 0;
        drawerOverlay.classList.add('active');

        // Set download buttons
        btnMd.href = `/api/reports/${encodeURIComponent(domain)}/download?format=markdown`;
        btnCsv.href = `/api/reports/${encodeURIComponent(domain)}/download?format=csv`;
        btnJson.href = `/api/reports/${encodeURIComponent(domain)}/download?format=json`;

        drawerDomain.textContent = domain;
        drawerBaseUrl.textContent = 'Loading...';
        sumPages.textContent = '-';
        sumWords.textContent = '-';
        sumImages.textContent = '-';
        sumReason.textContent = '-';
        sumTiming.textContent = '';
        contactsEmails.innerHTML = '';
        contactsPhones.innerHTML = '';
        contactsSocial.innerHTML = '';
        pagesList.innerHTML = 'Loading pages...';
        seoList.innerHTML = '';

        try {
            const res = await fetch(`/api/reports/${encodeURIComponent(domain)}`);
            if (!res.ok) throw new Error();
            const data = await res.json();

            drawerBaseUrl.textContent = data.base_url || '';
            sumPages.textContent = data.total_pages_crawled || 0;
            sumWords.textContent = (data.total_words || 0).toLocaleString();
            sumImages.textContent = data.total_images || 0;
            sumReason.textContent = data.crawl_stopped_reason || 'Finished';
            tabCount.textContent = data.total_page_count || 0;

            const start = data.crawl_started ? new Date(data.crawl_started).toLocaleString() : '';
            const end = data.crawl_finished ? new Date(data.crawl_finished).toLocaleString() : '';
            sumTiming.textContent = `${start} → ${end}`;

            // Contacts
            renderChips(contactsEmails, data.all_emails || [], 'email');
            renderChips(contactsPhones, data.all_phones || [], 'phone');
            renderSocial(contactsSocial, data.social_media || {});

            // Pages initial render
            renderPages(data.pages || []);
            currentPagesOffset = (data.pages || []).length;
            btnLoadMore.style.display = currentPagesOffset < (data.total_page_count || 0) ? 'block' : 'none';

            // SEO first 5
            renderSEO((data.pages || []).slice(0, 5));
        } catch (e) {
            showToast('Failed to load report detail', 'error');
        }
    }

    function renderChips(container, items, label) {
        container.innerHTML = '';
        if (items.length === 0) {
            container.innerHTML = `<span style="font-size:13px; color:var(--text-muted);">None discovered</span>`;
            return;
        }
        items.forEach(item => {
            const chip = document.createElement('span');
            chip.className = 'contact-chip';
            chip.textContent = item;
            chip.title = 'Click to copy';
            chip.addEventListener('click', () => {
                navigator.clipboard.writeText(item);
                showToast(`Copied ${item} to clipboard!`);
            });
            container.appendChild(chip);
        });
    }

    function renderSocial(container, social) {
        container.innerHTML = '';
        const keys = Object.keys(social);
        if (keys.length === 0) {
            container.innerHTML = `<span style="font-size:13px; color:var(--text-muted);">None discovered</span>`;
            return;
        }
        keys.forEach(k => {
            const a = document.createElement('a');
            a.className = 'contact-chip';
            a.textContent = `${k}: ${social[k]}`;
            a.href = social[k];
            a.target = '_blank';
            container.appendChild(a);
        });
    }

    function renderPages(pages) {
        pagesList.innerHTML = '';
        if (pages.length === 0) {
            pagesList.innerHTML = `<span style="color:var(--text-muted); font-size:13px;">No pages found</span>`;
            return;
        }
        pages.forEach(p => {
            const item = document.createElement('div');
            item.style.padding = '8px 12px';
            item.style.border = '1px solid var(--border-color)';
            item.style.borderRadius = '6px';
            item.style.background = 'var(--card-bg)';
            item.innerHTML = `
                <div style="font-weight:600; font-size:13px; color:var(--text-main);">${p.page_title || 'Untitled'}</div>
                <div style="font-size:11px; color:var(--primary); word-break:break-all;">${p.url || ''}</div>
                <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Depth: ${p.depth || 0} • Words: ${p.word_count || 0}</div>
            `;
            pagesList.appendChild(item);
        });
    }

    btnLoadMore.addEventListener('click', async () => {
        if (!currentDomain) return;
        try {
            const query = encodeURIComponent(pagesQuery.value.trim());
            const res = await fetch(`/api/reports/${encodeURIComponent(currentDomain)}/pages?offset=${currentPagesOffset}&limit=${PAGE_LIMIT}&query=${query}`);
            const data = await res.json();
            const newPages = data.pages || [];
            newPages.forEach(p => {
                const item = document.createElement('div');
                item.style.padding = '8px 12px';
                item.style.border = '1px solid var(--border-color)';
                item.style.borderRadius = '6px';
                item.style.background = 'var(--card-bg)';
                item.innerHTML = `
                    <div style="font-weight:600; font-size:13px; color:var(--text-main);">${p.page_title || 'Untitled'}</div>
                    <div style="font-size:11px; color:var(--primary); word-break:break-all;">${p.url || ''}</div>
                    <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Depth: ${p.depth || 0} • Words: ${p.word_count || 0}</div>
                `;
                pagesList.appendChild(item);
            });
            currentPagesOffset += newPages.length;
            if (currentPagesOffset >= (data.total || 0) || newPages.length === 0) {
                btnLoadMore.style.display = 'none';
            }
        } catch (e) {
            showToast('Failed to load more pages', 'error');
        }
    });

    pagesQuery.addEventListener('input', async () => {
        if (!currentDomain) return;
        const query = encodeURIComponent(pagesQuery.value.trim());
        try {
            const res = await fetch(`/api/reports/${encodeURIComponent(currentDomain)}/pages?offset=0&limit=${PAGE_LIMIT}&query=${query}`);
            const data = await res.json();
            renderPages(data.pages || []);
            currentPagesOffset = (data.pages || []).length;
            btnLoadMore.style.display = currentPagesOffset < (data.total || 0) ? 'block' : 'none';
        } catch (e) {}
    });

    function renderSEO(pages) {
        seoList.innerHTML = '';
        if (pages.length === 0) {
            seoList.innerHTML = `<span style="color:var(--text-muted); font-size:13px;">No SEO metadata found</span>`;
            return;
        }
        pages.forEach(p => {
            const box = document.createElement('div');
            box.style.padding = '10px 14px';
            box.style.border = '1px solid var(--border-color)';
            box.style.borderRadius = '6px';
            box.style.background = 'var(--bg-color)';
            box.innerHTML = `
                <div style="font-weight:600; font-size:13px; margin-bottom:4px;">${p.og_title || p.page_title || 'Untitled'}</div>
                <div style="font-size:12px; color:var(--text-muted); margin-bottom:4px;">${p.meta_description || 'No meta description'}</div>
                <div style="font-size:11px; color:var(--primary);">${p.canonical_url || p.url}</div>
            `;
            seoList.appendChild(box);
        });
    }

    searchInput.addEventListener('input', renderReports);
    sortSelect.addEventListener('change', renderReports);

    loadReports();
});
