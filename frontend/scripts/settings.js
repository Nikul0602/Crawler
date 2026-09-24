// Settings Page JavaScript
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
    // Theme toggle in topbar
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
            document.documentElement.dataset.theme = next;
            localStorage.setItem('crawlmaster-theme', next);
            if (settTheme) settTheme.value = next;
        });
    }

    // Tabs
    document.querySelectorAll('.settings-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.settings-pane').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const target = document.getElementById(`pane-${tab.dataset.tab}`);
            if (target) target.classList.add('active');
        });
    });

    // Form Crawler
    const formCrawler = document.getElementById('form-crawler');
    const settMaxPages = document.getElementById('sett-max-pages');
    const settMaxDepth = document.getElementById('sett-max-depth');
    const settConcurrent = document.getElementById('sett-concurrent');
    const settDelay = document.getElementById('sett-delay');
    const settMaxTime = document.getElementById('sett-max-time');
    const settRespectRobots = document.getElementById('sett-respect-robots');
    const btnResetDefaults = document.getElementById('btn-reset-defaults');

    // Form Pipeline
    const formPipeline = document.getElementById('form-pipeline');
    const stageCrawl4ai = document.getElementById('stage-crawl4ai');
    const stageScrapling = document.getElementById('stage-scrapling');
    const stageJina = document.getElementById('stage-jina');
    const stageCurlTls = document.getElementById('stage-curl-tls');
    const stageCurlRotated = document.getElementById('stage-curl-rotated');
    const stageHttpx = document.getElementById('stage-httpx');
    const settJinaKey = document.getElementById('sett-jina-key');
    const btnToggleKey = document.getElementById('btn-toggle-key');

    // Storage elements
    const storageOutput = document.getElementById('storage-output');
    const storageData = document.getElementById('storage-data');
    const checkpointStatus = document.getElementById('checkpoint-status');
    const btnClearCheckpoint = document.getElementById('btn-clear-checkpoint');

    // Appearance
    const settTheme = document.getElementById('sett-theme');
    const settPolling = document.getElementById('sett-polling');
    const btnSaveAppearance = document.getElementById('btn-save-appearance');

    btnToggleKey.addEventListener('click', () => {
        if (settJinaKey.type === 'password') {
            settJinaKey.type = 'text';
            btnToggleKey.textContent = 'Hide';
        } else {
            settJinaKey.type = 'password';
            btnToggleKey.textContent = 'Show';
        }
    });

    async function loadSettings() {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();

            // Crawler
            settMaxPages.value = data.max_pages ?? 200;
            settMaxDepth.value = data.max_depth ?? 5;
            settConcurrent.value = data.concurrent ?? 3;
            settDelay.value = data.delay ?? 1.5;
            settMaxTime.value = data.max_time ?? 30;
            settRespectRobots.checked = data.respect_robots ?? true;

            // Pipeline
            stageCrawl4ai.checked = data.enable_crawl4ai ?? true;
            stageScrapling.checked = data.enable_scrapling ?? true;
            stageJina.checked = data.enable_jina ?? true;
            stageCurlTls.checked = data.enable_curl_tls ?? true;
            stageCurlRotated.checked = data.enable_curl_rotated ?? true;
            stageHttpx.checked = data.enable_httpx ?? true;
            settJinaKey.value = data.jina_key_masked || '';

            // Appearance
            settTheme.value = data.theme || localStorage.getItem('crawlmaster-theme') || 'light';
            settPolling.value = data.polling_interval ?? 2;
        } catch (e) {
            showToast('Failed to load settings', 'error');
        }
    }

    async function loadStorage() {
        try {
            const res = await fetch('/api/system/storage');
            const data = await res.json();
            storageOutput.textContent = `${data.output_size_mb || 0} MB (${data.reports_count || 0} reports)`;
            storageData.textContent = `${data.data_size_mb || 0} MB`;
            checkpointStatus.textContent = data.checkpoint_exists ? 'Checkpoint file is active (can be resumed)' : 'No active checkpoint file';
            btnClearCheckpoint.disabled = !data.checkpoint_exists;
        } catch (e) {}
    }

    formCrawler.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            max_pages: parseInt(settMaxPages.value),
            max_depth: parseInt(settMaxDepth.value),
            concurrent: parseInt(settConcurrent.value),
            delay: parseFloat(settDelay.value),
            max_time: parseInt(settMaxTime.value),
            respect_robots: settRespectRobots.checked
        };
        await saveSettings(payload, 'Crawler defaults saved');
    });

    formPipeline.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
            enable_crawl4ai: stageCrawl4ai.checked,
            enable_scrapling: stageScrapling.checked,
            enable_jina: stageJina.checked,
            enable_curl_tls: stageCurlTls.checked,
            enable_curl_rotated: stageCurlRotated.checked,
            enable_httpx: stageHttpx.checked,
            jina_api_key: settJinaKey.value.trim()
        };
        await saveSettings(payload, 'Pipeline settings saved');
    });

    btnSaveAppearance.addEventListener('click', async () => {
        const theme = settTheme.value;
        document.documentElement.dataset.theme = theme;
        localStorage.setItem('crawlmaster-theme', theme);

        const payload = {
            theme: theme,
            polling_interval: parseInt(settPolling.value)
        };
        await saveSettings(payload, 'Appearance preferences saved');
    });

    btnResetDefaults.addEventListener('click', async () => {
        if (confirm('Reset all settings to default values?')) {
            try {
                const res = await fetch('/api/settings/reset', { method: 'POST' });
                if (res.ok) {
                    showToast('Settings reset to defaults');
                    loadSettings();
                }
            } catch (e) {
                showToast('Failed to reset settings', 'error');
            }
        }
    });

    btnClearCheckpoint.addEventListener('click', async () => {
        if (confirm('Clear the crawl checkpoint? Unfinished crawls will no longer be resumable.')) {
            try {
                const res = await fetch('/api/system/clear-checkpoint', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || 'Checkpoint cleared');
                loadStorage();
            } catch (e) {
                showToast('Failed to clear checkpoint', 'error');
            }
        }
    });

    async function saveSettings(payload, successMsg) {
        try {
            const res = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                showToast(successMsg);
                loadSettings();
            } else {
                showToast('Failed to save settings', 'error');
            }
        } catch (e) {
            showToast('Network error saving settings', 'error');
        }
    }

    loadSettings();
    loadStorage();
});
