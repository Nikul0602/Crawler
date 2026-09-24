document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const form = document.getElementById('crawl-form');
    const urlInput = document.getElementById('url-input');
    const modal = document.getElementById('crawl-modal');
    const btnStay = document.getElementById('btn-stay');
    const btnLeave = document.getElementById('btn-leave');
    const themeToggle = document.getElementById('theme-toggle');
    const tasksTableBody = document.getElementById('tasks-table-body');
    
    // Stats Elements
    const statTotal = document.getElementById('stat-total-crawls');
    const statPages = document.getElementById('stat-pages-crawled');
    const statSuccess = document.getElementById('stat-success-rate');
    const statAvg = document.getElementById('stat-avg-time');
    const statActive = document.getElementById('stat-active');
    
    // Polling Interval
    let pollingInterval = null;

    // Check Notifications Permission
    if ("Notification" in window && Notification.permission !== "granted" && Notification.permission !== "denied") {
        Notification.requestPermission();
    }

    // Only the notification bell icon remains a placeholder for now
    const notifBtn = document.querySelector('.icon-btn:not(.theme-toggle)');
    if (notifBtn) {
        notifBtn.addEventListener('click', (e) => {
            e.preventDefault();
            alert('Notifications panel coming soon!');
        });
    }

    // "View All Crawls" button → navigate to /crawls
    const viewAllBtn = document.querySelector('.btn-text');
    if (viewAllBtn) {
        viewAllBtn.addEventListener('click', (e) => {
            e.preventDefault();
            window.location.href = '/crawls';
        });
    }

    function updateThemeToggle() {
        const isDark = document.documentElement.dataset.theme === 'dark';
        const nextTheme = isDark ? 'light' : 'dark';
        themeToggle.setAttribute('aria-label', `Switch to ${nextTheme} theme`);
        themeToggle.setAttribute('title', `Switch to ${nextTheme} theme`);
    }

    themeToggle.addEventListener('click', () => {
        const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = nextTheme;
        localStorage.setItem('crawlmaster-theme', nextTheme);
        updateThemeToggle();
    });
    updateThemeToggle();

    // Submit form
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = urlInput.value.trim();
        if (!url) return;

        try {
            const res = await fetch('/api/crawl', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            
            if (data.task_id) {
                // Show modal
                modal.classList.add('active');
                urlInput.value = '';
                
                // Refresh table immediately
                fetchTasks();
            }
        } catch (error) {
            console.error('Failed to start crawl:', error);
            alert('Failed to start crawl. Check console for details.');
        }
    });

    // Modal Actions
    btnStay.addEventListener('click', () => {
        modal.classList.remove('active');
    });

    btnLeave.addEventListener('click', () => {
        modal.classList.remove('active');
        // Visually we stay on the page here because it's a SPA, but we reset view
        // In a real scenario, this might route back to dashboard root or close a tab
    });

    // Fetch and render tasks
    async function fetchTasks() {
        try {
            const res = await fetch('/api/tasks');
            const tasks = await res.json();
            renderTable(tasks);
            updateStats(tasks);
        } catch (error) {
            console.error('Failed to fetch tasks:', error);
        }
    }

    function renderTable(tasks) {
        tasksTableBody.innerHTML = '';
        
        if (tasks.length === 0) {
            tasksTableBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No recent crawls found.</td></tr>';
            return;
        }

        tasks.forEach(task => {
            const tr = document.createElement('tr');
            
            // Format name (just hostname)
            let hostname = task.url;
            try { hostname = new URL(task.url).hostname; } catch (e) {}
            const initial = hostname.charAt(0).toUpperCase();

            // Status rendering
            let statusClass = 'status-queued';
            let colorVar = 'var(--text-muted)';
            if (task.status === 'Completed') { statusClass = 'status-completed'; colorVar = 'var(--accent-green)'; }
            if (task.status === 'Running') { statusClass = 'status-running'; colorVar = 'var(--accent-blue)'; }
            if (task.status === 'Failed') { statusClass = 'status-failed'; colorVar = 'var(--accent-red)'; }

            // Progress rendering
            let progress = task.progress || 0;
            if (task.status === 'Completed') progress = 100;
            
            // Time rendering
            const started = new Date(task.started_at);
            const timeAgo = formatTimeAgo(started);
            const timeStr = started.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            let duration = task.duration || '--:--';
            
            // Check for completion notification trigger
            checkAndNotify(task);

            tr.innerHTML = `
                <td>
                    <div class="crawl-name-cell">
                        <div class="crawl-avatar" style="background-color: ${colorVar}">${initial}</div>
                        <div class="crawl-details">
                            <span class="crawl-title">${hostname}</span>
                            <span class="crawl-url">${task.url.length > 30 ? task.url.substring(0,30)+'...' : task.url}</span>
                        </div>
                    </div>
                </td>
                <td>
                    <div class="status-badge ${statusClass}">
                        <div class="status-dot"></div>
                        ${task.status}
                    </div>
                </td>
                <td class="td-progress">
                    <div class="progress-info">
                        <span>${progress}%</span>
                    </div>
                    <div class="table-progress-bar">
                        <div class="table-progress-fill" style="width: ${progress}%; background-color: ${colorVar}"></div>
                    </div>
                </td>
                <td>${task.pages_crawled || 0}</td>
                <td>
                    <div class="crawl-details">
                        <span>${timeAgo}</span>
                        <span class="crawl-url">${timeStr}</span>
                    </div>
                </td>
                <td>${duration}</td>
                <td>
                    <div class="table-actions">
                        <button class="action-btn" title="View Logs"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg></button>
                        <button class="action-btn" title="More Options"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"></circle><circle cx="12" cy="5" r="1"></circle><circle cx="12" cy="19" r="1"></circle></svg></button>
                    </div>
                </td>
            `;
            tasksTableBody.appendChild(tr);
        });
    }

    function updateStats(tasks) {
        statTotal.textContent = tasks.length;
        
        let totalPages = 0;
        let successCount = 0;
        let activeCount = 0;
        let totalDurationSecs = 0;
        let durationCount = 0;

        tasks.forEach(t => {
            totalPages += (t.pages_crawled || 0);
            if (t.status === 'Completed') successCount++;
            if (t.status === 'Running' || t.status === 'Queued') activeCount++;
            
            if (t.status === 'Completed' && t.duration) {
                // parse mm:ss
                const parts = t.duration.split(':');
                if (parts.length === 2) {
                    totalDurationSecs += (parseInt(parts[0]) * 60 + parseInt(parts[1]));
                    durationCount++;
                }
            }
        });

        statPages.textContent = totalPages > 1000 ? (totalPages/1000).toFixed(1) + 'K' : totalPages;
        
        const successRate = tasks.length > 0 ? ((successCount / tasks.length) * 100).toFixed(1) : 0;
        statSuccess.textContent = successRate + '%';
        
        statActive.textContent = activeCount;

        if (durationCount > 0) {
            const avgSecs = Math.floor(totalDurationSecs / durationCount);
            const m = Math.floor(avgSecs / 60);
            const s = avgSecs % 60;
            statAvg.textContent = `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
    }

    // Notifications logic
    const notifiedTasks = new Set();
    function checkAndNotify(task) {
        if (task.status === 'Completed' || task.status === 'Failed') {
            if (!notifiedTasks.has(task.id)) {
                // It just finished
                notifiedTasks.add(task.id);
                // Only notify if we didn't just load this from a long time ago
                // check if it completed in the last 10 seconds
                if (task.completed_at) {
                    const compTime = new Date(task.completed_at).getTime();
                    if (Date.now() - compTime < 10000) {
                        sendNotification(task);
                    }
                }
            }
        }
    }

    function sendNotification(task) {
        if ("Notification" in window && Notification.permission === "granted") {
            let hostname = task.url;
            try { hostname = new URL(task.url).hostname; } catch(e){}
            
            const title = task.status === 'Completed' ? 'Crawl Completed!' : 'Crawl Failed';
            const options = {
                body: `${hostname} finished crawling. Pages: ${task.pages_crawled}`,
                icon: 'https://cdn-icons-png.flaticon.com/512/1006/1006509.png'
            };
            new Notification(title, options);
        }
    }

    function formatTimeAgo(date) {
        const seconds = Math.floor((new Date() - date) / 1000);
        let interval = seconds / 31536000;
        if (interval > 1) return Math.floor(interval) + "y ago";
        interval = seconds / 2592000;
        if (interval > 1) return Math.floor(interval) + "mo ago";
        interval = seconds / 86400;
        if (interval > 1) return Math.floor(interval) + "d ago";
        interval = seconds / 3600;
        if (interval > 1) return Math.floor(interval) + "h ago";
        interval = seconds / 60;
        if (interval > 1) return Math.floor(interval) + "m ago";
        return Math.floor(seconds) + "s ago";
    }

    // Initial fetch and start polling
    fetchTasks();
    pollingInterval = setInterval(fetchTasks, 2000);
});


