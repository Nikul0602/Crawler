// Schedules Page JavaScript
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
    let allSchedules = [];
    let currentFilter = 'all';

    // Elements
    const tableBody = document.getElementById('schedules-table-body');
    const countAll = document.getElementById('count-all');
    const countActive = document.getElementById('count-active');
    const countPaused = document.getElementById('count-paused');
    const modal = document.getElementById('schedule-modal');
    const btnOpenModal = document.getElementById('btn-open-modal');
    const btnCancel = document.getElementById('btn-modal-cancel');
    const form = document.getElementById('schedule-form');
    const modalTitle = document.getElementById('modal-title');

    // Form inputs
    const schedId = document.getElementById('sched-id');
    const schedName = document.getElementById('sched-name');
    const schedUrl = document.getElementById('sched-url');
    const schedFreq = document.getElementById('sched-freq');
    const schedCron = document.getElementById('sched-cron');
    const cronGroup = document.getElementById('cron-group');
    const schedPages = document.getElementById('sched-pages');
    const schedDepth = document.getElementById('sched-depth');
    const schedNoRobots = document.getElementById('sched-norobots');

    schedFreq.addEventListener('change', () => {
        cronGroup.style.display = schedFreq.value === 'custom' ? 'block' : 'none';
    });

    btnOpenModal.addEventListener('click', () => {
        form.reset();
        schedId.value = '';
        modalTitle.textContent = 'New Crawl Schedule';
        cronGroup.style.display = 'none';
        modal.classList.add('active');
    });

    btnCancel.addEventListener('click', () => modal.classList.remove('active'));
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('active');
    });

    // Pills filtering
    document.querySelectorAll('.pill').forEach(pill => {
        pill.addEventListener('click', () => {
            document.querySelectorAll('.pill').forEach(p => p.classList.remove('active-pill'));
            pill.classList.add('active-pill');
            currentFilter = pill.dataset.filter;
            renderSchedules();
        });
    });

    async function loadSchedules() {
        try {
            const res = await fetch('/api/schedules');
            allSchedules = await res.json();
            updateCounts();
            renderSchedules();
        } catch (e) {
            showToast('Failed to load schedules', 'error');
        }
    }

    function updateCounts() {
        countAll.textContent = allSchedules.length;
        countActive.textContent = allSchedules.filter(s => s.enabled).length;
        countPaused.textContent = allSchedules.filter(s => !s.enabled).length;
    }

    function renderSchedules() {
        tableBody.innerHTML = '';
        let filtered = allSchedules.filter(s => {
            if (currentFilter === 'active') return s.enabled;
            if (currentFilter === 'paused') return !s.enabled;
            return true;
        });

        if (filtered.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--text-muted);">No schedules found. Click "New Schedule" to create one.</td></tr>`;
            return;
        }

        filtered.forEach(s => {
            const tr = document.createElement('tr');
            const nextRun = s.enabled ? (s.next_run_at ? new Date(s.next_run_at).toLocaleString() : 'Pending') : 'Paused';
            const lastRun = s.last_run_at ? new Date(s.last_run_at).toLocaleString() : 'Never';
            const statusBadge = s.last_run_status ? `<span style="font-size:11px; padding:2px 6px; border-radius:4px; margin-left:6px; background:${s.last_run_status === 'Completed' ? 'var(--soft-green)' : 'var(--soft-orange)'}; color:${s.last_run_status === 'Completed' ? 'var(--accent-green)' : 'var(--accent-orange)'};">${s.last_run_status}</span>` : '';

            tr.innerHTML = `
                <td>
                    <div style="font-weight:600; font-size:14px;">${s.name}</div>
                    <div style="font-size:12px; color:var(--text-muted);">${s.url}</div>
                </td>
                <td><span class="frequency-badge">${s.frequency}</span></td>
                <td>
                    <label class="toggle-switch">
                        <input type="checkbox" ${s.enabled ? 'checked' : ''} class="sched-toggle">
                        <span class="toggle-slider"></span>
                    </label>
                </td>
                <td style="font-size:13px;">${nextRun}</td>
                <td style="font-size:13px;">${lastRun} ${statusBadge}</td>
                <td>
                    <div class="table-actions">
                        <button class="action-btn btn-run" title="Run Now">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                        </button>
                        <button class="action-btn btn-edit" title="Edit">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </button>
                        <button class="action-btn btn-delete" title="Delete">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                        </button>
                    </div>
                </td>
            `;

            tr.querySelector('.sched-toggle').addEventListener('change', async () => {
                try {
                    await fetch(`/api/schedules/${s.id}/toggle`, { method: 'POST' });
                    showToast(`Schedule ${s.enabled ? 'paused' : 'activated'}`);
                    loadSchedules();
                } catch (e) {
                    showToast('Failed to toggle schedule', 'error');
                }
            });

            tr.querySelector('.btn-run').addEventListener('click', async () => {
                try {
                    const res = await fetch(`/api/schedules/${s.id}/run-now`, { method: 'POST' });
                    if (res.ok) {
                        showToast(`Crawl triggered for "${s.name}"!`);
                        loadSchedules();
                    } else {
                        showToast('Failed to run schedule', 'error');
                    }
                } catch (e) {
                    showToast('Network error triggering crawl', 'error');
                }
            });

            tr.querySelector('.btn-edit').addEventListener('click', () => {
                schedId.value = s.id;
                schedName.value = s.name;
                schedUrl.value = s.url;
                schedFreq.value = s.frequency;
                schedCron.value = s.cron_expr || '';
                cronGroup.style.display = s.frequency === 'custom' ? 'block' : 'none';
                schedPages.value = s.config?.max_pages || 200;
                schedDepth.value = s.config?.max_depth || 5;
                schedNoRobots.checked = !!s.config?.no_robots;
                modalTitle.textContent = 'Edit Crawl Schedule';
                modal.classList.add('active');
            });

            tr.querySelector('.btn-delete').addEventListener('click', async () => {
                if (confirm(`Delete schedule "${s.name}"?`)) {
                    try {
                        await fetch(`/api/schedules/${s.id}`, { method: 'DELETE' });
                        showToast('Schedule deleted');
                        loadSchedules();
                    } catch (e) {
                        showToast('Failed to delete schedule', 'error');
                    }
                }
            });

            tableBody.appendChild(tr);
        });
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            name: schedName.value.trim(),
            url: schedUrl.value.trim(),
            frequency: schedFreq.value,
            cron_expr: schedCron.value.trim(),
            config: {
                max_pages: parseInt(schedPages.value),
                max_depth: parseInt(schedDepth.value),
                no_robots: schedNoRobots.checked
            }
        };

        const id = schedId.value;
        const url = id ? `/api/schedules/${id}` : '/api/schedules';
        const method = id ? 'PUT' : 'POST';

        try {
            const res = await fetch(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                showToast(id ? 'Schedule updated' : 'Schedule created');
                modal.classList.remove('active');
                loadSchedules();
            } else {
                const err = await res.json();
                showToast(err.detail || 'Save failed', 'error');
            }
        } catch (err) {
            showToast('Network error saving schedule', 'error');
        }
    });

    loadSchedules();
    setInterval(loadSchedules, 15000);
});
