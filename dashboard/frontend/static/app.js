document.addEventListener('DOMContentLoaded', () => {
    // --- State & DOM Elements ---
    let socket = null;
    const logOutput = document.getElementById('log-output');
    const statusBadge = document.getElementById('status-indicator');
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const navItems = document.querySelectorAll('nav li');
    const pages = document.querySelectorAll('.page');
    const pageTitle = document.getElementById('page-title');
    const configForm = document.getElementById('config-form');

    // --- Navigation ---
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const pageId = item.getAttribute('data-page');

            // UI Update
            navItems.forEach(i => i.classList.remove('active'));
            item.classList.add('active');

            pages.forEach(p => p.classList.remove('active'));
            document.getElementById(`page-${pageId}`).classList.add('active');

            pageTitle.textContent = item.textContent;
        });
    });

    // --- WebSocket Logic (Log Streaming) ---
    function connectLogs() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(`${protocol}//${window.location.host}/ws/logs`);

        socket.onopen = () => {
            console.log('Log stream connected');
            appendLog('System: Connected to live telemetry stream.', 'system-msg');
        };

        socket.onmessage = (event) => {
            appendLog(event.data);
        };

        socket.onclose = () => {
            console.log('Log stream disconnected');
            appendLog('System: Stream disconnected. Retrying...', 'system-msg');
            setTimeout(connectLogs, 3000);
        };
    }

    function appendLog(text, className = '') {
        const span = document.createElement('span');
        if (className) span.className = className;
        span.textContent = text;
        logOutput.appendChild(span);
        logOutput.scrollTop = logOutput.scrollHeight;

        // Keep terminal light
        if (logOutput.children.length > 500) {
            logOutput.removeChild(logOutput.firstChild);
        }
    }

    // --- System Orchestration ---
    async function updateStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            if (data.running) {
                statusBadge.textContent = 'Running';
                statusBadge.className = 'status-badge running';
                btnStart.style.display = 'none';
                btnStop.style.display = 'block';
            } else {
                statusBadge.textContent = 'Stopped';
                statusBadge.className = 'status-badge stopped';
                btnStart.style.display = 'block';
                btnStop.style.display = 'none';
            }
        } catch (e) {
            console.error('Status check failed', e);
        }
    }

    btnStart.addEventListener('click', async () => {
        appendLog('System: Initiating boot sequence...', 'system-msg');
        await fetch('/api/start', { method: 'POST' });
        updateStatus();
    });

    btnStop.addEventListener('click', async () => {
        appendLog('System: Signal sent. Shutting down...', 'system-msg');
        await fetch('/api/stop', { method: 'POST' });
        updateStatus();
    });

    // --- Configuration ---
    async function loadConfig() {
        const res = await fetch('/api/config');
        const config = await res.json();

        // Fill form fields
        for (const [key, value] of Object.entries(config)) {
            const field = configForm.querySelector(`[name="${key}"]`);
            if (field) {
                if (field.tagName === 'SELECT') {
                    field.value = value;
                } else {
                    field.value = value;
                }
            }
        }

        // Update Stats card
        if (config.current_provider) {
            document.getElementById('stat-provider').textContent = config.current_provider.toUpperCase();
        }
    }

    configForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(configForm);
        const data = Object.fromEntries(formData.entries());

        // Handle comma-separated lists
        if (data.allowed_channels) data.allowed_channels = data.allowed_channels.split(',').map(s => parseInt(s.trim()));
        if (data.admin_nodes) data.admin_nodes = data.admin_nodes.split(',').map(s => s.trim());

        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        appendLog('System: Configuration saved.', 'system-msg');
        loadConfig();
    });

    // --- Initialization ---
    connectLogs();
    updateStatus();
    loadConfig();
    setInterval(updateStatus, 5000);
});
