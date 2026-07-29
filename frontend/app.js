/* ===== GhostBrowser App.js — Full Frontend Logic ===== */

const API = '';  // Same origin — FastAPI serves frontend

function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
}

window.XSRF_TOKEN = getCookie('XSRF-TOKEN') || '';

// --- Admin-token auth (in-memory only) ---
let _adminToken = '';

function isApiUrl(resource) {
    const url = typeof resource === 'string' ? resource : (resource?.url || '');
    return url.startsWith(`${API}/api/`) || url.startsWith('/api/');
}

function withAdminToken(headers) {
    if (!_adminToken) return headers;
    return { ...headers, 'X-Admin-Token': _adminToken };
}

// Transparently add the XSRF token to every non-GET/HEAD fetch.
const _originalFetch = window.fetch;
window.fetch = function (...args) {
    const [resource, rawInit = {}] = args;
    const init = { ...rawInit };
    const method = (init.method || (typeof resource === 'object' ? resource.method : 'GET') || 'GET').toUpperCase();
    if (window.XSRF_TOKEN && method !== 'GET' && method !== 'HEAD') {
        init.headers = { ...init.headers, 'X-XSRF-Token': window.XSRF_TOKEN };
    }
    if (isApiUrl(resource)) {
        init.headers = withAdminToken(init.headers);
    }
    args = [resource, init];
    return _originalFetch.apply(this, args);
};

// --- Transparently add admin token to legacy XHR calls targeting /api/ ---
(function () {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
        this._gbApiUrl = String(url);
        return originalOpen.call(this, method, url, ...rest);
    };

    XMLHttpRequest.prototype.send = function (...args) {
        if (_adminToken && isApiUrl(this._gbApiUrl)) {
            originalSetRequestHeader.call(this, 'X-Admin-Token', _adminToken);
        }
        return originalSend.apply(this, args);
    };
})();

function showAdminTokenPrompt(message) {
    return new Promise((resolve) => {
        const existing = document.getElementById('admin-token-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.id = 'admin-token-modal';
        modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:99999;';
        modal.innerHTML = `
            <div style="background:var(--surface,#1f2937);padding:1.5rem;border-radius:8px;min-width:320px;max-width:90vw;box-shadow:0 20px 25px -5px rgba(0,0,0,0.3);font-family:sans-serif;">
                <h3 style="margin:0 0 0.5rem 0;color:var(--text);">Admin Token Required</h3>
                ${message ? `<p style="color:var(--danger,#ef4444);font-size:0.85rem;margin:0.25rem 0;">${escHtml(message)}</p>` : ''}
                <p style="color:var(--text-muted);font-size:0.85rem;margin:0.25rem 0;">The backend is protected by <code style="background:rgba(255,255,255,0.1);padding:0.1rem 0.3rem;border-radius:4px;">GHOSTBROWSER_ADMIN_TOKEN</code>. Your token stays in memory only and is never persisted.</p>
                <input id="admin-token-input" type="password" placeholder="Paste X-Admin-Token" autocomplete="off" style="width:100%;padding:0.5rem;margin:0.5rem 0;background:var(--input-bg);border:1px solid var(--border);border-radius:4px;color:var(--text);box-sizing:border-box;">
                <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.75rem;">
                    <button id="admin-token-cancel" style="padding:0.4rem 0.8rem;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text-muted);cursor:pointer;">Skip</button>
                    <button id="admin-token-submit" style="padding:0.4rem 0.8rem;border-radius:4px;border:none;background:var(--primary,#6366f1);color:#fff;cursor:pointer;">Authenticate</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const input = document.getElementById('admin-token-input');
        const submit = () => {
            const token = input.value.trim();
            modal.remove();
            resolve(token);
        };
        document.getElementById('admin-token-submit').addEventListener('click', submit);
        document.getElementById('admin-token-cancel').addEventListener('click', () => {
            modal.remove();
            resolve(null);
        });
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        input.focus();
    });
}

async function verifyAdminToken(token) {
    try {
        const res = await _originalFetch(`${API}/api/profiles`, { headers: { 'X-Admin-Token': token } });
        return res.status !== 401;
    } catch (_) {
        return true;
    }
}

async function ensureAdminToken() {
    if (_adminToken) return true;

    // First contact: a 401 means the backend requires a token.
    let needsAuth;
    try {
        const res = await _originalFetch(`${API}/api/profiles`);
        needsAuth = res.status === 401;
    } catch (_) {
        return true;
    }
    if (!needsAuth) return true;

    let promptMessage = '';
    while (true) {
        const token = await showAdminTokenPrompt(promptMessage);
        if (!token) return false;
        if (await verifyAdminToken(token)) {
            _adminToken = token;
            return true;
        }
        promptMessage = 'Invalid token — please try again.';
    }
}

async function ensureXsrfToken() {
    if (window.XSRF_TOKEN) return;
    try {
        const res = await _originalFetch(`${API}/api/system/csrf-token`);
        if (!res.ok) return;
        const data = await res.json();
        window.XSRF_TOKEN = getCookie('XSRF-TOKEN') || data.token || '';
    } catch (_) {
        // Cookie-less fallthrough; later GETs will set the cookie.
    }
}

// =========================================================
// STATE
// =========================================================
let allProfiles = [];
let logFilter = 'all';
let createdProfileId = null;
let activityLog = [];
let chipState = { canvas: true, webgl: true, audio: true };
const testedProxyValues = Object.create(null);

let useVirtualKeyboard = false;
let vkScrambled = false;
let vkShift = false;
let vkCaps = false;
let vkCurrentInput = null;

async function requestJson(url, options = {}, fallbackMessage = 'Request failed') {
    options = options || {};
    const method = (options.method || 'GET').toUpperCase();
    if (window.XSRF_TOKEN && method !== 'GET' && method !== 'HEAD') {
        options.headers = { ...options.headers, 'X-XSRF-Token': window.XSRF_TOKEN };
    }
    const response = await fetch(url, options);
    let payload = {};
    try {
        payload = await response.json();
    } catch (_) {
        payload = {};
    }
    if (!response.ok) {
        const detail = payload.detail || payload.message || `${fallbackMessage} (HTTP ${response.status})`;
        throw new Error(detail);
    }
    return payload;
}

function resetProxyTest(inputId, resultId, connectButtonId = null) {
    delete testedProxyValues[inputId];
    const result = document.getElementById(resultId);
    if (result) result.textContent = '';
    const connectButton = connectButtonId ? document.getElementById(connectButtonId) : null;
    if (connectButton) connectButton.disabled = true;
}

function proxyWasTested(inputId) {
    const input = document.getElementById(inputId);
    return Boolean(input && input.value.trim() && testedProxyValues[inputId] === input.value.trim());
}

async function testEnteredProxy(inputId, resultId, connectButtonId = null) {
    const input = document.getElementById(inputId);
    const result = document.getElementById(resultId);
    const proxyString = input ? input.value.trim() : '';
    resetProxyTest(inputId, resultId, connectButtonId);
    if (!proxyString) {
        if (result) result.textContent = 'Enter a proxy first.';
        showToast('Enter a proxy first.', 'warning');
        return false;
    }
    if (result) result.textContent = 'Testing connection...';
    try {
        const data = await requestJson('/api/proxies/test-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy_string: proxyString })
        }, 'Proxy test failed');
        testedProxyValues[inputId] = proxyString;
        if (result) result.textContent = data.message;
        const connectButton = connectButtonId ? document.getElementById(connectButtonId) : null;
        if (connectButton) connectButton.disabled = false;
        showToast(data.message, 'success');
        return true;
    } catch (error) {
        if (result) result.textContent = error.message;
        showToast(error.message, 'error');
        return false;
    }
}

// =========================================================
// NAVIGATION
// =========================================================
const PAGE_TITLES = {
    dashboard: 'Dashboard',
    profiles: 'Profiles',
    automation: 'Automation Hub',
    proxies: 'Proxies',
    'ai-status': 'AI Status',
    logs: 'Live Logs',
    settings: 'Settings'
};

function navigate(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    const el = document.getElementById(`page-${page}`);
    if (el) el.classList.add('active');

    const nav = document.querySelector(`[data-page="${page}"]`);
    if (nav) nav.classList.add('active');

    document.getElementById('page-title').textContent = PAGE_TITLES[page] || page;

    // Trigger page-specific loads
    if (page === 'profiles') fetchProfiles();
    if (page === 'automation') switchAutomationTab('macros');
    if (page === 'ai-status') fetchCFStatus();
    if (page === 'proxies') { fetchProxies(); fetchTitanProxies(); }
}

// =========================================================
// METRICS & DASHBOARD
// =========================================================
async function fetchMetrics() {
    try {
        const data = await requestJson(`${API}/api/metrics`, {}, 'Metrics unavailable');

        document.getElementById('stat-active').textContent = `${data.active_profiles} / ${data.total_profiles}`;
        document.getElementById('stat-quarantine').textContent = data.quarantined_profiles;
        document.getElementById('stat-ram').textContent = `${data.memory_usage_percent.toFixed(1)}%`;
        document.getElementById('topbar-ram').textContent = `${data.memory_usage_percent.toFixed(1)}%`;
        const osSelect = document.getElementById('new-profile-os');
        if (osSelect && data.host_os) {
            const icon = data.host_os === 'Mac' ? '🍎' : (data.host_os === 'Linux' ? '🐧' : '🪟');
            osSelect.innerHTML = `<option value="${escAttr(data.host_os)}">${icon} ${escHtml(data.host_os)} (Host matched)</option>`;
            osSelect.disabled = true;
            osSelect.title = 'Profiles use the host operating system to prevent cross-OS fingerprint contradictions.';
        }
        const credentialStatus = document.getElementById('setting-credential-store');
        if (credentialStatus && data.credential_store) {
            credentialStatus.value = data.credential_store.configured
                ? `Windows DPAPI protected — ${data.credential_store.count} accounts`
                : 'Protected credential store is not configured';
        }

        const health = document.getElementById('system-health');
        const dot = health.querySelector('.health-dot');
        if (data.memory_usage_percent > 85) {
            dot.className = 'health-dot critical';
            health.style.background = 'rgba(239,68,68,0.1)';
            health.style.borderColor = 'rgba(239,68,68,0.2)';
            health.style.color = 'var(--danger)';
            health.querySelector('span').textContent = 'System Critical';
        } else {
            dot.className = 'health-dot healthy';
            health.style.background = '';
            health.style.borderColor = '';
            health.style.color = '';
            health.querySelector('span').textContent = 'System Healthy';
        }
    } catch (e) {
        const health = document.getElementById('system-health');
        if (health) {
            const dot = health.querySelector('.health-dot');
            if (dot) dot.className = 'health-dot offline';
            health.style.background = 'rgba(239,68,68,0.1)';
            health.style.borderColor = 'rgba(239,68,68,0.2)';
            health.style.color = 'var(--danger)';
            const label = health.querySelector('span');
            if (label) label.textContent = 'Backend Offline';
        }
    }
}

async function fetchCFStatus() {
    try {
        const res = await fetch(`${API}/api/cloudflare/status`);
        if (!res.ok) return;
        const data = await res.json();

        // Sidebar badge
        document.getElementById('sidebar-cf-count').textContent = data.healthy_count;

        // Dashboard ring
        const pct = data.total_accounts > 0 ? (data.healthy_count / data.total_accounts) : 0;
        const circumference = 301.59;
        const offset = circumference * (1 - pct);
        const arc = document.getElementById('cf-ring-arc');
        if (arc) {
            arc.style.strokeDashoffset = offset;
            arc.style.transition = 'stroke-dashoffset 1s ease';
        }
        const pctEl = document.getElementById('cf-ring-pct');
        if (pctEl) pctEl.textContent = `${Math.round(pct * 100)}%`;

        const legendH = document.getElementById('cf-legend-healthy');
        const legendC = document.getElementById('cf-legend-cooldown');
        if (legendH) legendH.textContent = `${data.healthy_count} Healthy`;
        if (legendC) legendC.textContent = `${data.cooldown_count} On Cooldown`;

        // Stat cards
        const h = document.getElementById('stat-cf-healthy');
        if (h) h.textContent = `${data.healthy_count} / ${data.total_accounts}`;
        const trend = document.getElementById('stat-cf-trend');
        if (trend) trend.textContent = pct > 0.5 ? 'OK' : 'LOW';

        // AI Status page
        const hCount = document.getElementById('cf-healthy-count');
        if (hCount) hCount.textContent = data.healthy_count;
        const cCount = document.getElementById('cf-cooldown-count');
        if (cCount) cCount.textContent = data.cooldown_count;
        const tCount = document.getElementById('cf-total-count');
        if (tCount) tCount.textContent = data.total_accounts;
        const pCount = document.getElementById('cf-priority-count');
        if (pCount) pCount.textContent = `${data.healthy_priority_count} / ${data.priority_count}`;

        // Account list on AI Status page
        renderCFAccounts(data);

    } catch (e) { /* backend not running */ }
}

function renderCFAccounts(data) {
    const list = document.getElementById('cf-accounts-list');
    if (!list) return;

    if (data.total_accounts === 0) {
        list.innerHTML = '<div class="empty-state-small">No accounts loaded. Add accounts to cloudflare_accounts.txt</div>';
        return;
    }

    const items = [];

    // HIGH-01 FIX: API returns data.accounts[] not data.healthy_accounts[] / data.cooldown_accounts[]
    // Iterating data.accounts and filtering by status fixes the permanently blank AI Status page.
    (data.accounts || []).forEach(acc => {
        const isCooling = acc.status === 'cooldown';
        const mins = isCooling ? Math.ceil((acc.cooldown_remaining_seconds || 0) / 60) : 0;
        items.push(`
            <div class="cf-account-item">
                <div class="cf-status ${isCooling ? 'cooldown' : 'healthy'}">${isCooling ? '⏳ Cooldown' : '✓ Healthy'}</div>
                ${acc.priority ? '<div class="cf-priority-badge">Priority</div>' : ''}
                <div class="cf-account-id">${escHtml(acc.account_id)}</div>
                ${isCooling ? `<div class="cf-cooldown-timer">${mins}m left</div>` : ''}
            </div>
        `);
    });

    list.innerHTML = items.join('');
}

// =========================================================
// PROFILES
// =========================================================
async function fetchProfiles() {
    try {
        const res = await fetch(`${API}/api/profiles`);
        if (!res.ok) return;
        allProfiles = await res.json();

        document.getElementById('profile-count-label').textContent = `${allProfiles.length} profile${allProfiles.length !== 1 ? 's' : ''}`;
        document.getElementById('sidebar-profile-count').textContent = allProfiles.length;

        renderProfiles(allProfiles);
        applyRiskBadges(allProfiles);
    } catch (e) { /* backend offline */ }
}

function safeProfileColor(value) {
    const color = String(value || '').toUpperCase();
    return /^#[0-9A-F]{6}$/.test(color) ? color : '#6366F1';
}

function renderProfiles(profiles) {
    const grid = document.getElementById('profiles-grid');
    if (!grid) return;

    if (profiles.length === 0) {
        grid.innerHTML = '';
        document.querySelector('.table-container').style.display = 'none';
        const emptyState = document.getElementById('profiles-empty');
        if (emptyState) emptyState.style.display = 'flex';
        return;
    }

    document.querySelector('.table-container').style.display = 'block';
    const emptyState = document.getElementById('profiles-empty');
    if (emptyState) emptyState.style.display = 'none';

    const displayedProfiles = [...profiles].sort(
        (left, right) => Number(Boolean(right.pinned)) - Number(Boolean(left.pinned))
    );

    grid.innerHTML = displayedProfiles.map(p => {
        const isRunning = p.status === 'Running';
        const id = String(p.id || '');
        const initials = escHtml((p.name || 'P').slice(0, 2).toUpperCase());
        const os = String(p.advanced?.os || p.os || '?');
        const osEmoji = os === 'Mac' ? '🍎' : '🪟';
        const proxy = typeof p.proxy === 'string' ? p.proxy : (p.proxy?.server || 'No Proxy');
        const idShort = id ? id.split('-')[0] : 'N/A';
        const profileColor = safeProfileColor(p.color);
        const tagElements = (Array.isArray(p.tags) ? p.tags : []).map(t => `<span class="profile-tag">${escHtml(t)}</span>`).join('');
        const proxyPinBadge = p.has_pinned_proxy ? `<span title="A fixed proxy override is configured" class="proxy-pin-badge">🔒</span>` : '';
        const pinLockBadge = p.has_pin ? `<span title="PIN protected" class="pin-lock-badge">🔒</span>` : '';
        const privacyMode = String(p.advanced?.privacy_mode || p.privacy_mode || 'standard');
        const privacyBadge = privacyMode === 'strict' ? 'St' : (privacyMode === 'ephemeral' ? 'E' : 'S');
        const privacyLabel = privacyMode.charAt(0).toUpperCase() + privacyMode.slice(1);
        const pinTitle = p.pinned ? 'Unpin profile' : 'Pin profile to top';

        return `
            <tr id="card-${escAttr(id)}" class="profile-row ${p.pinned ? 'profile-row-pinned' : ''}" style="--profile-color:${profileColor}">
                <td><input type="checkbox" class="profile-checkbox" value="${escAttr(id)}" data-action="update-bulk-actions"></td>
                <td>
                    <div class="td-name">
                        <div class="profile-icon-wrapper">${initials}</div>
                        <div>
                            <div style="display: flex; align-items: center; gap: 4px;">
                                ${escHtml(p.name)}
                                <button class="btn-icon profile-pin-button ${p.pinned ? 'active' : ''}" data-action="toggle-profile-pin" data-profile-id="${escAttr(id)}" data-pinned="${p.pinned ? 'false' : 'true'}" title="${pinTitle}" aria-label="${pinTitle}">
                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="${p.pinned ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2"><path d="M12 17v5M5 3h14l-3 7 3 4H5l3-4-3-7z"/></svg>
                                </button>
                                <button class="btn-icon profile-edit-button" data-action="open-edit-modal" data-profile-id="${escAttr(id)}" title="Rename or edit profile settings" aria-label="Rename or edit profile settings">
                                    ⚙️
                                </button>
                            </div>
                            <div style="display: flex; gap: 4px; margin-top: 4px;">
                                <span class="td-id">${escHtml(idShort)}</span>
                                ${proxyPinBadge}
                                ${pinLockBadge}
                                ${p.pinned ? '<span class="pinned-label">Pinned</span>' : ''}
                            </div>
                            <div class="profile-tag-list">${tagElements}</div>
                        </div>
                    </div>
                </td>
                <td>${tagElements || '<span class="td-id">No tags</span>'}</td>
                <td>
                    <div class="status-indicator ${isRunning ? 'running' : 'stopped'}">
                        ${isRunning ? '<div class="status-dot"></div>' : ''}
                        ${isRunning ? 'Running' : 'Stopped'}
                    </div>
                </td>
                <td>
                    <span class="privacy-badge" title="Privacy mode: ${escHtml(privacyLabel)}">${escHtml(privacyBadge)}</span>
                    <select class="privacy-select" data-action="set-privacy-mode" data-profile-id="${escAttr(id)}" title="Change privacy mode">
                        <option value="standard" ${privacyMode === 'standard' ? 'selected' : ''}>Standard</option>
                        <option value="strict" ${privacyMode === 'strict' ? 'selected' : ''}>Strict</option>
                        <option value="ephemeral" ${privacyMode === 'ephemeral' ? 'selected' : ''}>Ephemeral</option>
                    </select>
                </td>
                <td><span class="td-proxy">${escHtml(proxy)}</span></td>
                <td>
                    <div class="td-os">${osEmoji} ${escHtml(os)}</div>
                </td>
                <td class="td-actions">
                    ${isRunning
                        ? `<button class="btn-secondary btn-sm" data-action="stop-profile" data-profile-id="${escAttr(id)}">⏹ Stop</button>`
                        : `<button class="btn-primary btn-sm" data-action="launch-profile" data-profile-id="${escAttr(id)}">▶ Launch</button>`}
                    <button class="btn-secondary btn-sm" data-action="scan-profile" data-profile-id="${escAttr(id)}" title="Scan Fingerprint Risk" aria-label="Scan fingerprint risk" style="padding: 0.25rem 0.5rem; color: var(--primary);">🛡️</button>
                    <button class="btn-secondary btn-sm" data-action="open-metadata-modal" data-profile-id="${escAttr(id)}" title="Tags, notes and pinning" aria-label="Edit tags, notes and pinning" style="padding: 0.25rem 0.5rem;">🏷️</button>
                    <button class="btn-secondary btn-sm" data-action="tag-profile" data-profile-id="${escAttr(id)}" title="Quick tag" aria-label="Quick tag" style="padding: 0.25rem 0.5rem;">+ Tag</button>
                    <button class="btn-secondary btn-sm" data-action="clone-profile" data-profile-id="${escAttr(id)}" title="Clone Profile" aria-label="Clone profile">🧬</button>
                    <button class="btn-secondary btn-sm" data-action="open-cookie-modal" data-profile-id="${escAttr(id)}" title="Manage Cookies" aria-label="Manage cookies">🍪</button>
                    <button class="btn-secondary btn-sm" data-action="open-set-pin-modal" data-profile-id="${escAttr(id)}" title="Set PIN" aria-label="Set PIN">🔒</button>
                    <button class="btn-icon stop" data-action="delete-profile" data-profile-id="${escAttr(id)}" title="Delete Profile" aria-label="Delete profile">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

async function toggleProfilePin(id, pinned) {
    try {
        await requestJson(`${API}/api/profiles/${id}/metadata`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pinned: Boolean(pinned) })
        }, 'Could not update profile pin');
        showToast(pinned ? 'Profile pinned to the top' : 'Profile unpinned', 'success');
        await fetchProfiles();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// --- EDIT PROFILE (FULL SETTINGS) ---
let currentEditProfileId = null;

function switchEditModalTab(tab) {
    document.getElementById('edit-tab-overview').style.display = 'none';
    document.getElementById('edit-tab-network').style.display = 'none';
    document.getElementById('edit-tab-stealth').style.display = 'none';
    document.getElementById('edit-tab-btn-overview').classList.remove('active');
    document.getElementById('edit-tab-btn-network').classList.remove('active');
    document.getElementById('edit-tab-btn-stealth').classList.remove('active');

    document.getElementById(`edit-tab-${tab}`).style.display = 'block';
    document.getElementById(`edit-tab-btn-${tab}`).classList.add('active');
}

function toggleEditChip(chipName) {
    const el = document.getElementById(`edit-chip-${chipName}`);
    el.classList.toggle('active');
}

function closeEditModal() {
    document.getElementById('edit-modal').classList.remove('show');
    currentEditProfileId = null;
}

async function openEditModal(id) {
    currentEditProfileId = id;
    switchEditModalTab('overview');

    try {
        const res = await fetch(`${API}/api/profiles`);
        const data = await res.json();
        const p = data.find(x => x.id === id);
        if (p) {
            document.getElementById('edit-profile-name').value = p.name || '';
            document.getElementById('edit-profile-locale').value = p.locale || '';
            document.getElementById('edit-profile-timezone').value = p.timezone || '';

            // Credentials are intentionally redacted. Blank preserves the
            // existing proxy; entering a value explicitly replaces it.
            const proxyInput = document.getElementById('edit-profile-proxy');
            proxyInput.value = '';
            proxyInput.disabled = false;
            proxyInput.placeholder = p.proxy?.authenticated
                ? 'Authenticated proxy configured — blank keeps it'
                : (p.proxy?.server ? `${p.proxy.server} configured — blank keeps it` : 'ip:port or ip:port:user:pass');
            document.getElementById('edit-clear-proxy').checked = false;
            document.getElementById('edit-proxy-test-button').disabled = false;
            document.getElementById('edit-proxy-connect-button').textContent = '2. Connect Tested Proxy';
            resetProxyTest('edit-profile-proxy', 'edit-proxy-test-result', 'edit-proxy-connect-button');
            document.getElementById('edit-proxy-test-result').textContent = p.proxy?.server
                ? `Current connection: ${p.proxy.server}. Enter a replacement to test it.`
                : 'Current connection: Direct (no proxy).';

            // Advanced
            const adv = p.advanced || {};
            let webrtc_val = adv.webrtc_mode || 'protected';
            if (webrtc_val === 'altered') {
                webrtc_val = 'protected';
            } else if (webrtc_val === 'real' || webrtc_val === 'disabled') {
                webrtc_val = 'protected';
                showToast('Warning: Legacy unsafe WebRTC mode detected. You must save this profile to upgrade it to native protected mode.', 'warning');
            }
            document.getElementById('edit-profile-webrtc').value = webrtc_val;

            if (adv.canvas_noise !== false) document.getElementById('edit-chip-canvas').classList.add('active');
            else document.getElementById('edit-chip-canvas').classList.remove('active');

            if (adv.webgl_noise !== false) document.getElementById('edit-chip-webgl').classList.add('active');
            else document.getElementById('edit-chip-webgl').classList.remove('active');

            if (adv.audio_noise !== false) document.getElementById('edit-chip-audio').classList.add('active');
            else document.getElementById('edit-chip-audio').classList.remove('active');

            if (adv.headless === true) document.getElementById('edit-chip-headless').classList.add('active');
            else document.getElementById('edit-chip-headless').classList.remove('active');

            document.getElementById('edit-modal').classList.add('show');
        }
    } catch(e) {
        showToast('Error loading profile: ' + e.message, 'error');
    }
}

function toggleEditProxyRemoval() {
    const clearProxy = document.getElementById('edit-clear-proxy').checked;
    const input = document.getElementById('edit-profile-proxy');
    const testButton = document.getElementById('edit-proxy-test-button');
    const connectButton = document.getElementById('edit-proxy-connect-button');
    resetProxyTest('edit-profile-proxy', 'edit-proxy-test-result', 'edit-proxy-connect-button');
    input.disabled = clearProxy;
    testButton.disabled = clearProxy;
    connectButton.disabled = !clearProxy;
    connectButton.textContent = clearProxy ? 'Use Direct Connection' : 'Connect Tested Proxy';
    if (clearProxy) {
        input.value = '';
        document.getElementById('edit-proxy-test-result').textContent = 'The saved proxy will be removed.';
    }
}

async function persistEditedProxy() {
    if (!currentEditProfileId) return null;
    const clearProxy = document.getElementById('edit-clear-proxy').checked;
    const proxyRaw = document.getElementById('edit-profile-proxy').value.trim();
    if (!clearProxy && !proxyRaw) return null;
    if (!clearProxy && !proxyWasTested('edit-profile-proxy')) {
        throw new Error('Test this exact proxy successfully before connecting it.');
    }
    return requestJson(`${API}/api/profiles/${currentEditProfileId}/proxy`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            proxy_string: clearProxy ? null : proxyRaw,
            clear_proxy: clearProxy
        })
    }, 'Could not connect proxy');
}

async function connectEditedProxy() {
    try {
        const result = await persistEditedProxy();
        if (!result) {
            showToast('Enter a proxy or choose direct connection first.', 'warning');
            return;
        }
        showToast(result.message, 'success');
        const resultElement = document.getElementById('edit-proxy-test-result');
        if (resultElement) resultElement.textContent = result.message;
        const proxyInput = document.getElementById('edit-profile-proxy');
        proxyInput.value = '';
        proxyInput.disabled = false;
        document.getElementById('edit-clear-proxy').checked = false;
        document.getElementById('edit-proxy-test-button').disabled = false;
        document.getElementById('edit-proxy-connect-button').textContent = '2. Connect Tested Proxy';
        resetProxyTest('edit-profile-proxy', 'edit-proxy-test-result', 'edit-proxy-connect-button');
        if (resultElement) resultElement.textContent = result.message;
        await fetchProfiles();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveProfileEdits() {
    if (!currentEditProfileId) return;

    const name = document.getElementById('edit-profile-name').value.trim();
    if (!name) {
        showToast('Name is required', 'error');
        return;
    }

    const proxyRaw = document.getElementById('edit-profile-proxy').value.trim();
    const clearProxy = document.getElementById('edit-clear-proxy').checked;
    if (proxyRaw && !proxyWasTested('edit-profile-proxy')) {
        showToast('Test this exact proxy successfully before connecting it.', 'warning');
        return;
    }

    const payload = {
        name: name,
        locale: document.getElementById('edit-profile-locale').value.trim() || null,
        timezone: document.getElementById('edit-profile-timezone').value.trim() || null,
        advanced: {
            webrtc_mode: document.getElementById('edit-profile-webrtc').value,
            canvas_noise: document.getElementById('edit-chip-canvas').classList.contains('active'),
            webgl_noise: document.getElementById('edit-chip-webgl').classList.contains('active'),
            audio_noise: document.getElementById('edit-chip-audio').classList.contains('active'),
            headless: document.getElementById('edit-chip-headless').classList.contains('active')
        }
    };

    try {
        if (proxyRaw || clearProxy) {
            const proxyResult = await persistEditedProxy();
            if (proxyResult) showToast(proxyResult.message, 'success');
        }
        await requestJson(`${API}/api/profiles/${currentEditProfileId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }, 'Failed to update profile settings');
        showToast('Profile updated successfully', 'success');
        closeEditModal();
        fetchProfiles();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// --- PIN LOCK ---
let currentPinProfileId = null;
let pinModalMode = 'set'; // 'set' or 'prompt'
let pinPromptLaunchId = null;

function openSetPinModal(id) {
    currentPinProfileId = id;
    pinModalMode = 'set';
    pinPromptLaunchId = null;
    document.getElementById('pin-profile-id').value = id;
    document.getElementById('pin-input').value = '';
    document.getElementById('pin-confirm').value = '';
    document.getElementById('pin-error').textContent = '';
    document.getElementById('pin-confirm-group').style.display = 'block';
    document.getElementById('pin-remove-btn').style.display = 'inline-block';
    const p = allProfiles.find(x => x.id === id);
    document.getElementById('pin-modal-title').textContent = `${p?.name || 'Profile'} PIN 🔒`;
    document.getElementById('pin-save-btn').textContent = 'Save PIN';
    document.getElementById('pin-modal').classList.add('show');
}

function openPinPrompt(id) {
    pinModalMode = 'prompt';
    pinPromptLaunchId = id;
    currentPinProfileId = null;
    document.getElementById('pin-profile-id').value = id;
    document.getElementById('pin-input').value = '';
    document.getElementById('pin-confirm').value = '';
    document.getElementById('pin-error').textContent = '';
    document.getElementById('pin-confirm-group').style.display = 'none';
    document.getElementById('pin-remove-btn').style.display = 'none';
    document.getElementById('pin-modal-title').textContent = 'Enter PIN to Launch 🔒';
    document.getElementById('pin-save-btn').textContent = 'Unlock & Launch';
    document.getElementById('pin-modal').classList.add('show');
}

function closePinModal() {
    document.getElementById('pin-modal').classList.remove('show');
    pinModalMode = 'set';
    pinPromptLaunchId = null;
    currentPinProfileId = null;
}

async function saveProfilePin() {
    const err = document.getElementById('pin-error');
    if (pinModalMode === 'prompt') {
        const pin = document.getElementById('pin-input').value;
        if (!pin) { err.textContent = 'Enter a PIN.'; return; }
        try {
            const res = await requestJson(`${API}/api/profiles/${pinPromptLaunchId}/pin/verify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pin })
            }, 'PIN verification failed');
            if (res.verified) {
                closePinModal();
                launchProfile(pinPromptLaunchId, pin);
            } else {
                err.textContent = 'Incorrect PIN.';
            }
        } catch (e) {
            err.textContent = e.message;
        }
        return;
    }

    if (!currentPinProfileId) return;
    const pin = document.getElementById('pin-input').value;
    const confirm = document.getElementById('pin-confirm').value;
    if (!pin) { err.textContent = 'Enter a PIN.'; return; }
    if (pin !== confirm) { err.textContent = 'PINs do not match.'; return; }
    try {
        await requestJson(`${API}/api/profiles/${currentPinProfileId}/pin/set`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin })
        }, 'Could not set PIN');
        showToast('PIN set', 'success');
        closePinModal();
        fetchProfiles();
    } catch (e) {
        err.textContent = e.message;
    }
}

async function clearProfilePin() {
    if (!currentPinProfileId) return;
    if (!confirm('Remove the PIN lock from this profile?')) return;
    try {
        await requestJson(`${API}/api/profiles/${currentPinProfileId}/pin`, {
            method: 'DELETE'
        }, 'Could not remove PIN');
        showToast('PIN removed', 'success');
        closePinModal();
        fetchProfiles();
    } catch (e) {
        document.getElementById('pin-error').textContent = e.message;
    }
}

// --- METADATA (TAGS/PROXY PIN) ---
let currentMetadataProfileId = null;

async function openMetadataModal(id) {
    currentMetadataProfileId = id;
    document.getElementById('metadata-modal').classList.add('show');
    try {
        const data = await requestJson(`${API}/api/profiles`, {}, 'Could not load profile metadata');
        // HIGH-02 FIX: API returns flat array, not {profiles: [...]}. Use data.find() directly.
        const p = Array.isArray(data) ? data.find(x => x.id === id) : null;
        if (p) {
            document.getElementById('meta-profile-id').value = id;
            document.getElementById('meta-tags').value = Array.isArray(p.tags) ? p.tags.join(', ') : '';
            document.getElementById('meta-notes').value = p.notes || '';
            document.getElementById('meta-profile-pinned').checked = Boolean(p.pinned);
            const pinInput = document.getElementById('meta-proxy-pin');
            pinInput.value = '';
            pinInput.placeholder = p.has_pinned_proxy ? 'Proxy pin configured — blank keeps it' : 'ip:port or ip:port:user:pass';
            document.getElementById('meta-clear-proxy-pin').checked = false;
            resetProxyTest('meta-proxy-pin', 'meta-proxy-test-result');
        }
    } catch(e) {
        closeMetadataModal();
        showToast(e.message, 'error');
    }
}

function closeMetadataModal() {
    document.getElementById('metadata-modal').classList.remove('show');
    currentMetadataProfileId = null;
}

async function saveMetadata() {
    if(!currentMetadataProfileId) return;
    const tags = [...new Set(document.getElementById('meta-tags').value.split(',').map(tag => tag.trim()).filter(Boolean))];
    const notes = document.getElementById('meta-notes').value.trim();
    const pinned = document.getElementById('meta-profile-pinned').checked;
    const proxyPin = document.getElementById('meta-proxy-pin').value.trim();
    const clearProxyPin = document.getElementById('meta-clear-proxy-pin').checked;
    if (proxyPin && !clearProxyPin && !proxyWasTested('meta-proxy-pin')) {
        showToast('Test this exact sticky proxy successfully before saving it.', 'warning');
        return;
    }
    const payload = { tags, notes, pinned, clear_proxy_pin: clearProxyPin };
    if (proxyPin && !clearProxyPin) payload.proxy_pin = proxyPin;

    try {
        await requestJson(`${API}/api/profiles/${currentMetadataProfileId}/metadata`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }, 'Failed to update metadata');
        showToast('Profile updated', 'success');
        closeMetadataModal();
        fetchProfiles();
    } catch(e) {
        showToast(e.message || 'Failed to update metadata', 'error');
    }
}

// --- CLONE PROFILE ---
async function tagProfilePrompt(id) {
    const raw = prompt('Enter tags to add, separated by commas:');
    if (raw === null) return;
    const tags = [...new Set(raw.split(',').map(t => t.trim()).filter(Boolean))];
    if (!tags.length) {
        showToast('No tags entered', 'warning');
        return;
    }
    try {
        await requestJson(`${API}/api/profiles/${id}/tags`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ add: tags, remove: [] })
        }, 'Failed to add tags');
        showToast(`Tagged profile with ${tags.length} tag(s)`, 'success');
        fetchProfiles();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function cloneProfile(id) {
    if(!confirm("Are you sure you want to duplicate this profile?")) return;
    showToast('Cloning profile...', 'info');
    try {
        const res = await fetch(`${API}/api/profiles/${id}/clone`, { method: 'POST' });
        if(res.ok) {
            showToast('Profile Cloned!', 'success');
            fetchProfiles();
        } else {
            showToast('Failed to clone', 'error');
        }
    } catch(e) {
        showToast('Clone error: ' + e.message, 'error');
    }
}

async function scanProfile(id) {
    document.getElementById('scan-modal').classList.add('show');
    document.getElementById('scan-loading').style.display = 'block';
    document.getElementById('scan-results').style.display = 'none';

    try {
        const res = await fetch(`${API}/api/profiles/${id}/scan`);
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            document.getElementById('scan-loading').style.display = 'none';
            document.getElementById('scan-results').style.display = 'block';

            const scan = data.scan;
            const score = scan.ai_score || 0;
            const circle = document.getElementById('scan-circle');
            const offset = 100 - score;
            circle.style.strokeDasharray = `${score}, 100`;

            // Color based on score
            if (score > 80) circle.style.stroke = 'var(--primary)'; // Green
            else if (score > 50) circle.style.stroke = '#fbbf24'; // Yellow
            else circle.style.stroke = 'var(--danger)'; // Red

            document.getElementById('scan-score').textContent = score;
            document.getElementById('scan-verdict').textContent = scan.overall_verdict || 'Unknown';
            document.getElementById('scan-reason').textContent = scan.reasoning || '';

            // Breakdown
            const issues = scan.detected_issues || [];
            const breakdownHtml = issues.length > 0
                ? issues.map(i => `<div style="color:var(--danger);">- ${escHtml(i)}</div>`).join('')
                : '<div style="color:var(--primary);">No major issues detected.</div>';
            document.getElementById('scan-breakdown').innerHTML = breakdownHtml;

        } else {
            closeScanModal();
            showToast('Scan failed: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch(e) {
        closeScanModal();
        showToast('Error running scan', 'error');
    }
}

function closeScanModal() {
    document.getElementById('scan-modal').classList.remove('show');
}

// Cookie Management Logic
let currentCookieProfileId = null;

async function openCookieModal(id) {
    currentCookieProfileId = id;
    const modal = document.getElementById('cookie-modal');
    const textarea = document.getElementById('cookie-textarea');
    textarea.value = "Fetching cookies... Please wait.";
    modal.classList.add('show');

    try {
        const res = await fetch(`${API}/api/profiles/${id}/cookies`);
        const data = await res.json();
        if (data.status === 'success') {
            textarea.value = JSON.stringify(data.cookies, null, 2);
        } else {
            textarea.value = "Error: " + data.message;
        }
    } catch (e) {
        textarea.value = "Network error: " + e.message;
    }
}

function closeCookieModal() {
    document.getElementById('cookie-modal').classList.remove('show');
    currentCookieProfileId = null;
}

function copyCookies() {
    const textarea = document.getElementById('cookie-textarea');
    textarea.select();
    document.execCommand('copy');
    showToast('Cookies copied to clipboard', 'success');
}

async function saveCookies() {
    if (!currentCookieProfileId) return;
    const textarea = document.getElementById('cookie-textarea');
    let cookies;
    try {
        cookies = JSON.parse(textarea.value);
        if (!Array.isArray(cookies)) throw new Error("Cookies must be an array");
    } catch (e) {
        showToast('Invalid JSON format: ' + e.message, 'error');
        return;
    }

    showToast('Saving and importing cookies...', 'info');
    try {
        const res = await fetch(`${API}/api/profiles/${currentCookieProfileId}/cookies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cookies: cookies })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast('Cookies imported successfully!', 'success');
            closeCookieModal();
        } else {
            showToast('Error: ' + data.message, 'error');
        }
    } catch (e) {
        showToast('Network error: ' + e.message, 'error');
    }
}

// Bulk Actions Logic
function toggleSelectAll() {
    const isChecked = document.getElementById('select-all').checked;
    const checkboxes = document.querySelectorAll('.profile-checkbox');
    checkboxes.forEach(cb => {
        // only check if row is visible (handle search filter)
        const tr = cb.closest('tr');
        if (tr && tr.style.display !== 'none') {
            cb.checked = isChecked;
        }
    });
    updateBulkActions();
}

function updateBulkActions() {
    const checked = document.querySelectorAll('.profile-checkbox:checked').length;
    const bulkDiv = document.getElementById('bulk-actions');
    if (checked > 0) {
        bulkDiv.style.display = 'flex';
    } else {
        bulkDiv.style.display = 'none';
        document.getElementById('select-all').checked = false;
    }
    updateAutomationSelectionCounts();
}

async function bulkLaunch() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return;
    for (const id of checked) {
        launchProfile(id); // Doesn't wait, launches in parallel
    }
}

async function bulkPinSelected() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return;
    let pinnedCount = 0;
    for (const id of checked) {
        try {
            await requestJson(`${API}/api/profiles/${id}/metadata`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pinned: true })
            }, 'Could not pin profile');
            pinnedCount++;
        } catch (error) {
            showToast(`${id.slice(0, 8)}: ${error.message}`, 'error');
        }
    }
    showToast(`${pinnedCount} profile${pinnedCount === 1 ? '' : 's'} pinned to the top`, 'success');
    fetchProfiles();
}

async function bulkDelete() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return;
    if (!confirm(`Are you sure you want to delete ${checked.length} profiles?`)) return;

    let deletedCount = 0;
    for (const id of checked) {
        try {
            await requestJson(`${API}/api/profiles/${id}`, { method: 'DELETE' }, 'Profile deletion failed');
            deletedCount++;
        } catch (e) {
            showToast(`${id.slice(0, 8)}: ${e.message}`, 'error');
        }
    }
    fetchProfiles();

    const isAutoReplenish = localStorage.getItem('auto_replenish') === 'true';
    if (isAutoReplenish && deletedCount > 0) {
        showToast(`Auto-replenishing ${deletedCount} profile(s)...`, 'info');
        try {
            await requestJson(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_name: "AutoReplenish", count: deletedCount })
            }, 'Auto-replenish failed');
            fetchProfiles();
            showToast(`Auto-replenished ${deletedCount} profile(s).`, 'success');
        } catch (e) { showToast(e.message, 'error'); }
    }
}

// Modal Tabs Logic
// HIGH-05 FIX: Accept 'el' parameter instead of relying on implicit global 'event' object.
// The implicit 'event' fails in strict mode and Firefox.
function switchModalTab(tabName, el) {
    const form = document.getElementById('modal-form');
    form.querySelectorAll('.modal-tabs .modal-tab').forEach(t => t.classList.remove('active'));
    form.querySelectorAll('[id^="tab-"]').forEach(c => c.classList.remove('active'));
    if (el) el.classList.add('active');
    const content = document.getElementById('tab-' + tabName);
    if (content) content.classList.add('active');
}

function filterProfiles() {
    const q = document.getElementById('profile-search').value.toLowerCase();
    const filtered = allProfiles.filter(p =>
        p.name.toLowerCase().includes(q) ||
        p.id.toLowerCase().includes(q) ||
        (p.advanced?.os || '').toLowerCase().includes(q) ||
        (Array.isArray(p.tags) ? p.tags : []).some(tag => String(tag).toLowerCase().includes(q))
    );
    renderProfiles(filtered);
}

async function launchProfile(id, pin = null) {
    addActivity(`Launching profile ${id.split('-')[0]}...`, 'info');
    try {
        const options = { method: 'POST' };
        if (pin) {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify({ pin });
        }
        const res = await fetch(`${API}/api/profiles/${id}/launch`, options);
        const d = await res.json();

        if (!res.ok) {
            if (res.status === 403) {
                openPinPrompt(id);
                return;
            }
            showToast(d.detail || 'Launch failed', 'error');
        } else {
            if (d.warning) {
                showToast(`Profile launched with warning: ${d.warning}`, 'warning');
                addActivity(`Proxy Failover: ${d.warning}`, 'warning');
            } else {
                showToast('Profile launched!', 'success');
            }
            addActivity(`Profile ${id.split('-')[0]} is now running`, 'success');
        }
        fetchProfiles();
    } catch (e) { showToast('Launch error: ' + e.message, 'error'); }
}

async function stopProfile(id) {
    try {
        await requestJson(`${API}/api/profiles/${id}/close`, { method: 'POST' }, 'Profile could not be stopped');
        showToast('Profile stopped', 'warning');
        addActivity(`Profile ${id.split('-')[0]} stopped`, 'warning');
        fetchProfiles();
    } catch (e) { showToast('Stop error: ' + e.message, 'error'); }
}

async function deleteProfile(id) {
    if (!confirm('Delete this profile? All data will be permanently lost.')) return;

    let success = false;
    try {
        await requestJson(`${API}/api/profiles/${id}`, { method: 'DELETE' }, 'Profile deletion failed');
        showToast('Profile deleted', 'warning');
        addActivity(`Profile ${id.split('-')[0]} deleted`, 'warning');
        fetchProfiles();
        success = true;
    } catch (e) { showToast('Delete error: ' + e.message, 'error'); }

    if (success && localStorage.getItem('auto_replenish') === 'true') {
        showToast('Auto-replenishing 1 profile...', 'info');
        try {
            await requestJson(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_name: "AutoReplenish", count: 1 })
            }, 'Auto-replenish failed');
            fetchProfiles();
            showToast('Auto-replenished 1 profile.', 'success');
        } catch (e) { showToast(e.message, 'error'); }
    }
}

async function setPrivacyMode(id, mode) {
    try {
        await requestJson(`${API}/api/profiles/${id}/privacy-mode`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ privacy_mode: mode })
        }, 'Could not update privacy mode');
        showToast(`Privacy mode set to ${mode}`, 'success');
        await fetchProfiles();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// =========================================================
// CREATE PROFILE MODAL
// =========================================================
function openCreateModal() {
    document.getElementById('create-modal').classList.add('active');
    showModalSection('modal-form');
    const overviewTab = document.querySelector('#modal-form .modal-tab');
    switchModalTab('overview', overviewTab);
    resetProxyTest('new-profile-proxy', 'new-proxy-test-result');
}

function closeCreateModal() {
    document.getElementById('create-modal').classList.remove('active');
    // Reset form
    setTimeout(() => {
        document.getElementById('new-profile-name').value = '';
        document.getElementById('new-profile-proxy').value = '';
        document.getElementById('new-profile-pin').value = '';
        document.getElementById('new-privacy-mode').value = 'standard';
        resetProxyTest('new-profile-proxy', 'new-proxy-test-result');
        showModalSection('modal-form');
        createdProfileId = null;
    }, 300);
}

function showModalSection(id) {
    ['modal-form', 'modal-progress', 'modal-success', 'modal-error'].forEach(s => {
        const el = document.getElementById(s);
        if (el) el.style.display = s === id ? '' : 'none';
    });
}

function toggleChip(el, key) {
    chipState[key] = !chipState[key];
    el.classList.toggle('active', chipState[key]);
}

function backToForm() { showModalSection('modal-form'); }

async function applyProfileTemplate() {
    const val = document.getElementById('new-profile-template').value;
    const canvasChip = document.getElementById('chip-canvas');
    const webglChip = document.getElementById('chip-webgl');
    const audioChip = document.getElementById('chip-audio');
    const headlessChip = document.getElementById('chip-headless');
    const trackersChip = document.getElementById('chip-trackers');
    const webrtc = document.getElementById('new-profile-webrtc');

    if (val === 'ecommerce') {
        canvasChip.classList.add('active');
        webglChip.classList.add('active');
        audioChip.classList.add('active');
        headlessChip.classList.remove('active');
        trackersChip.classList.add('active');
        webrtc.value = 'protected';
    } else if (val === 'social') {
        canvasChip.classList.remove('active'); // some social flags canvas noise
        webglChip.classList.add('active');
        audioChip.classList.add('active');
        headlessChip.classList.remove('active');
        trackersChip.classList.remove('active');
        webrtc.value = 'protected';
    } else if (val === 'research') {
        canvasChip.classList.remove('active');
        webglChip.classList.remove('active');
        audioChip.classList.remove('active');
        headlessChip.classList.add('active');
        trackersChip.classList.add('active');
        webrtc.value = 'protected';
    }
}

async function submitCreateProfile() {
    const name = document.getElementById('new-profile-name').value.trim();
    if (!name) {
        showToast('Please enter a profile name.', 'warning');
        return;
    }

    const proxyRaw = document.getElementById('new-profile-proxy').value.trim();
    if (proxyRaw && !proxyWasTested('new-profile-proxy')) {
        showToast('Test this exact proxy successfully before creating the profile.', 'warning');
        switchModalTab('network', document.querySelector('#modal-form .modal-tab:nth-child(2)'));
        return;
    }
    const count = parseInt(document.getElementById('new-profile-count').value) || 1;

    const pinRaw = document.getElementById('new-profile-pin').value.trim() || null;

    const payload = {
        name: name,
        proxy_string: proxyRaw || null,
        pin: pinRaw,
        advanced: {
            os: document.getElementById('new-profile-os').value,
            webrtc_mode: document.getElementById('new-profile-webrtc').value,
            canvas_noise: document.getElementById('chip-canvas').classList.contains('active'),
            webgl_noise: document.getElementById('chip-webgl').classList.contains('active'),
            audio_noise: document.getElementById('chip-audio').classList.contains('active'),
            headless: document.getElementById('chip-headless').classList.contains('active'),
            block_trackers: document.getElementById('chip-trackers') ? document.getElementById('chip-trackers').classList.contains('active') : false,
            privacy_mode: document.getElementById('new-privacy-mode').value,
            cpu_cores: 8,
            memory_gb: 16,
            screen_resolution: '1920x1080'
        }
    };

    // Show progress
    showModalSection('modal-progress');
    resetProgressSteps();

    addLogLine(`Starting validated profile creation (${count > 1 ? 'Bulk Mode: ' + count + ' profiles' : 'Single Mode'})...`);

    setStepActive(1);
    addLogLine('Calling Kimi AI via Cloudflare Workers...');

    let profile;
    const requestController = new AbortController();
    const requestTimeout = setTimeout(() => requestController.abort(), 120000);
    try {
        let resPromise;
        if (count > 1) {
            // Bulk creation
            resPromise = fetch(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: requestController.signal,
                body: JSON.stringify({
                    base_name: name,
                    count: count,
                    proxy_string: proxyRaw || null,
                    advanced: payload.advanced
                })
            });
        } else {
            // Single creation
            resPromise = fetch(`${API}/api/profiles/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: requestController.signal,
                body: JSON.stringify(payload)
            });
        }

        addLogLine('Request submitted. Waiting for the backend result; no unverified test claims will be shown.');
        const res = await resPromise;
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
            setStepError(1);
            const detail = data.detail || data.message || `Profile creation failed (HTTP ${res.status})`;
            addLogLine(`❌ Error: ${detail}`);
            document.getElementById('modal-error-msg').textContent = detail;
            showModalSection('modal-error');
            addActivity(`Profile creation failed: ${detail}`, 'error');
            addLogEntry('error', detail);
            return;
        }

        profile = data;
        const failedResults = count > 1 && Array.isArray(profile.results)
            ? profile.results.filter(result => result.status !== 'success')
            : [];
        if (count > 1 && (profile.status === 'partial' || profile.status === 'error' || failedResults.length > 0)) {
            for (let step = 1; step <= 3; step++) setStepDone(step);
            setStepError(4);
            const detail = data.message || 'Bulk creation completed only partially.';
            addLogLine(`❌ ${detail}`);
            document.getElementById('modal-error-msg').textContent = `${detail}. Review the Profiles list for any profiles that were created.`;
            showModalSection('modal-error');
            addActivity(detail, 'warning');
            return;
        }

        for (let step = 1; step <= 4; step++) setStepDone(step);
        addLogLine('✅ Backend creation and validation completed successfully.');
        createdProfileId = count === 1 ? (profile.id || null) : null;

    } catch (e) {
        setStepError(1);
        addLogLine(`❌ Network error: ${e.message}`);
        const detail = e.name === 'AbortError'
            ? 'Profile generation timed out after 120 seconds. Please retry; exhausted Kimi accounts will be skipped automatically.'
            : 'Could not reach backend: ' + e.message;
        document.getElementById('modal-error-msg').textContent = detail;
        showModalSection('modal-error');
        return;
    } finally {
        clearTimeout(requestTimeout);
    }

    // Show success
    await delay(400);
    const preview = document.getElementById('success-profile-preview');
    const launchButton = document.getElementById('launch-created-profile-btn');
    if (launchButton) launchButton.style.display = createdProfileId ? 'inline-flex' : 'none';
    if (count > 1) {
        if (preview) {
            preview.innerHTML = `
                <b>Bulk Creation Complete</b><br>
                ${escHtml(profile.message || 'Multiple profiles created successfully.')}<br>
                Check the Profiles list to view them.
            `;
        }
        addActivity(`Bulk created ${profile.success_count} profiles`, 'success');
        addLogEntry('info', `Bulk profile creation finished.`);
    } else {
        if (preview) {
            preview.innerHTML = `
                Profile ID: ${escHtml(profile.id)}<br>
                OS: ${escHtml(profile.advanced?.os || profile.os || 'AI Generated')}<br>
                GPU: ${escHtml((profile.advanced?.webgl_renderer || profile.webgl_renderer || 'AI Generated').slice(0,50))}<br>
                Timezone: ${escHtml(profile.timezone || 'AI Generated')}<br>
                Locale: ${escHtml(profile.locale || 'AI Generated')}
            `;
        }
        addActivity(`New AI profile "${profile.name}" created`, 'success');
        addLogEntry('info', `Profile "${profile.name}" created with ID ${profile.id}`);
    }

    showModalSection('modal-success');
    fetchProfiles();
}

async function launchNewProfile() {
    if (createdProfileId) {
        closeCreateModal();
        navigate('profiles');
        await delay(300);
        launchProfile(createdProfileId);
    }
}

// Progress step helpers
function resetProgressSteps() {
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`step-${i}`);
        if (!step) continue;
        step.className = 'progress-step pending';
        const icon = step.querySelector('.step-icon');
        icon.className = 'step-icon';
        icon.textContent = '';
    }
    document.getElementById('progress-log').textContent = '';
}

function setStepActive(n) {
    const step = document.getElementById(`step-${n}`);
    if (!step) return;
    step.className = 'progress-step active';
    const icon = step.querySelector('.step-icon');
    icon.className = 'step-icon spinner';
    icon.textContent = '';
}

function setStepDone(n) {
    const step = document.getElementById(`step-${n}`);
    if (!step) return;
    step.className = 'progress-step done';
    const icon = step.querySelector('.step-icon');
    icon.className = 'step-icon done-icon';
    icon.textContent = '✓';
}

function setStepError(n) {
    const step = document.getElementById(`step-${n}`);
    if (!step) return;
    step.className = 'progress-step error-step';
    const icon = step.querySelector('.step-icon');
    icon.className = 'step-icon error-icon';
    icon.textContent = '✕';
}

function addLogLine(msg) {
    const el = document.getElementById('progress-log');
    if (!el) return;
    el.textContent += msg + '\n';
    el.scrollTop = el.scrollHeight;
}

// =========================================================
// PROXIES
// =========================================================
async function fetchProxies() {
    try {
        const res = await fetch(`${API}/api/proxies`);
        if (!res.ok) return;
        const proxies = await res.json();

        document.getElementById('proxy-total-badge').textContent = proxies.length;

        const list = document.getElementById('proxy-list');
        if (!list) return;

        if (proxies.length === 0) {
            list.innerHTML = '<div class="empty-state-small">No proxies loaded.</div>';
            return;
        }

        list.innerHTML = proxies.map(p => `
            <div class="proxy-item">
                <span class="mono">${escHtml(p.server)}</span>
                ${p.authenticated ? `<span class="proxy-status alive">Auth</span>` : `<span class="proxy-status alive">Open</span>`}
            </div>
        `).join('');
    } catch (e) { /* offline */ }
}

async function fetchTitanProxies() {
    try {
        const res = await fetch(`${API}/api/proxies/titan`);
        if (!res.ok) return;
        const data = await res.json();

        const grid = document.getElementById('titan-proxies-grid');
        if (!grid) return;

        if (!data.proxies || data.proxies.length === 0) {
            grid.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:2rem;color:var(--text-muted);">No proxies found in Titan Database. Run the scraper first.</td></tr>';
            return;
        }

        grid.innerHTML = data.proxies.map(p => {
            let statusBadge = '<span class="cf-status healthy">Online</span>';
            let pingColor = 'var(--success)';

            if (p.latency_ms > 500) {
                pingColor = 'var(--warning)';
                statusBadge = '<span class="cf-status cooldown">Slow</span>';
            }
            if (p.status === 'dead' || p.latency_ms === -1) {
                pingColor = 'var(--danger)';
                statusBadge = '<span style="color:var(--danger);font-size:0.75rem;font-weight:600;background:rgba(239,68,68,0.1);padding:2px 6px;border-radius:4px;">Dead</span>';
            }

            return `
                <tr>
                    <td class="mono">${escHtml(p.ip)}</td>
                    <td class="mono">${escHtml(p.port)}</td>
                    <td><span style="background:rgba(255,255,255,0.1);padding:2px 6px;border-radius:4px;font-size:0.75rem;">${escHtml(String(p.protocol || 'unknown').toUpperCase())}</span></td>
                    <td>${escHtml(p.city || 'Unknown')}, ${escHtml(p.country || 'Unknown')}</td>
                    <td class="mono" style="color:${pingColor};">${escHtml(p.latency_ms)}ms</td>
                    <td>${statusBadge}</td>
                </tr>
            `;
        }).join('');
    } catch(e) {}
}

async function importProxies() {
    const text = document.getElementById('proxy-import-text').value.trim();
    if (!text) { showToast('Paste proxy list first.', 'warning'); return; }

    const lines = text.split('\n').filter(l => l.trim());
    const proxies = [];

    for (const line of lines) {
        const parts = line.trim().split(':');
        if (parts.length === 2) {
            proxies.push({ server: `http://${parts[0]}:${parts[1]}` });
        } else if (parts.length === 4) {
            proxies.push({ server: `http://${parts[0]}:${parts[1]}`, username: parts[2], password: parts[3] });
        }
    }

    if (proxies.length === 0) { showToast('No valid proxies found.', 'error'); return; }

    try {
        const res = await fetch(`${API}/api/proxies`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxies })
        });
        if (res.ok) {
            const data = await res.json();
            showToast(`${data.added} proxies imported!`, 'success');
            addActivity(`${data.added} proxies added to pool`, 'success');
            document.getElementById('proxy-import-text').value = '';
            fetchProxies();
        }
    } catch (e) { showToast('Import failed: ' + e.message, 'error'); }
}

async function testAllProxies() {
    // LOW-02 FIX: Actually call the proxy health check API instead of just showing a toast
    const btn = document.getElementById('btn-test-proxies');
    if (btn) { btn.disabled = true; btn.textContent = 'Testing...'; }
    showToast('Running health check on all proxies...', 'info');
    try {
        const res = await fetch(`${API}/api/proxies/test`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showToast(data.message || 'Proxy health check complete', 'success');
            addActivity(data.message || 'Proxy health check complete', 'success');
            fetchProxies();
        } else {
            showToast('Health check failed', 'error');
        }
    } catch(e) {
        showToast('Health check failed: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Test All'; }
    }
}

async function scrapeFreeProxies() {
    const btn = document.getElementById('btn-scrape-proxies');
    const originalText = btn.innerHTML;

    btn.innerHTML = `<div class="spinner" style="width:16px;height:16px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:8px;"></div> Scraping & Testing...`;
    btn.disabled = true;
    showToast('Auto-scraper started. This will take a few minutes...', 'info');

    try {
        const res = await fetch(`${API}/api/proxies/scrape`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_count: 50 })
        });

        if (res.ok) {
            const data = await res.json();
            showToast(data.message, 'success');
            addActivity(data.message, 'success');
            fetchProxies();
        } else {
            const err = await res.json();
            showToast('Scraping failed: ' + err.detail, 'error');
        }
    } catch (e) {
        showToast('Network error during scrape: ' + e.message, 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

// =========================================================
// LOGS
// =========================================================
const logStore = [];
const SAFE_LOG_LEVELS = new Set(['info', 'warning', 'error', 'success']);

function safeUiClass(value, allowed, fallback = 'info') {
    const normalized = String(value ?? '').toLowerCase();
    return allowed.has(normalized) ? normalized : fallback;
}

function addLogEntry(level, msg) {
    const entry = {
        level: safeUiClass(level, SAFE_LOG_LEVELS),
        msg: String(msg ?? ''),
        time: new Date().toLocaleTimeString()
    };
    logStore.push(entry);
    renderLogs();
}

function renderLogs() {
    const stream = document.getElementById('log-stream');
    if (!stream) return;

    const filtered = logFilter === 'all' ? logStore : logStore.filter(e => e.level === logFilter);

    if (filtered.length === 0) {
        stream.innerHTML = '<div class="log-entry info"><span class="log-ts">--:--:--</span><span class="log-msg">No entries matching filter.</span></div>';
        return;
    }

    stream.innerHTML = filtered.map(e => `
        <div class="log-entry ${safeUiClass(e.level, SAFE_LOG_LEVELS)}">
            <span class="log-ts">${escHtml(e.time)}</span>
            <span class="log-level">${escHtml(String(e.level).toUpperCase())}</span>
            <span class="log-msg">${escHtml(e.msg)}</span>
        </div>
    `).join('');

    stream.scrollTop = stream.scrollHeight;
}

function setLogFilter(filter, btn) {
    logFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderLogs();
}

function clearLogs() { logStore.length = 0; renderLogs(); }

function exportLogs() {
    const text = logStore.map(e => `[${e.time}] ${e.level.toUpperCase()}: ${e.msg}`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `ghostbrowser-logs-${Date.now()}.txt`;
    a.click();
}

// =========================================================
// ACTIVITY FEED
// =========================================================
function addActivity(msg, type = 'info') {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    const empty = feed.querySelector('.activity-empty');
    if (empty) empty.remove();

    const safeType = safeUiClass(type, SAFE_LOG_LEVELS);
    const item = document.createElement('div');
    item.className = 'activity-item';
    item.innerHTML = `
        <div class="activity-dot ${safeType}"></div>
        <span>${escHtml(msg)}</span>
        <span class="activity-time">${new Date().toLocaleTimeString()}</span>
    `;

    feed.insertBefore(item, feed.firstChild);

    // Keep max 20 items
    while (feed.children.length > 20) feed.removeChild(feed.lastChild);

    // Also log
    addLogEntry(safeType === 'success' ? 'info' : safeType, msg);
}

// =========================================================
// SETTINGS
// =========================================================
async function saveSettings() {
    const maxConcurrent = document.getElementById('setting-max-concurrent').value;
    try {
        await requestJson(`${API}/api/rotator/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_concurrent: parseInt(maxConcurrent) })
        }, 'Settings could not be saved');
        showToast('Settings saved!', 'success');
        addLogEntry('info', `Settings saved: max_concurrent=${maxConcurrent}`);
    } catch (e) { showToast('Save failed: ' + e.message, 'error'); }
}

// =========================================================
// TOAST
// =========================================================
function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    const safeType = safeUiClass(type, SAFE_LOG_LEVELS);
    toast.className = `toast ${safeType}`;

    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${escHtml(msg)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// =========================================================
// MACRO MANAGEMENT
// =========================================================
let currentMacros = [];
let currentSchedules = [];

function openRunMacroModal() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return showToast('Select at least one profile first', 'error');

    const select = document.getElementById('run-macro-select');
    select.innerHTML = currentMacros.map(m => `<option value="${escAttr(m.id)}">${escHtml(m.name)}</option>`).join('');

    if (currentMacros.length === 0) {
        select.innerHTML = '<option disabled>No macros available. Create one first.</option>';
    }

    document.getElementById('run-macro-text').textContent = `Select a macro to run on ${checked.length} profile(s).`;
    document.getElementById('run-macro-modal').classList.add('show');
}

function closeRunMacroModal() {
    document.getElementById('run-macro-modal').classList.remove('show');
}

async function executeBulkMacro() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    const macroId = document.getElementById('run-macro-select').value;

    if (checked.length === 0 || !macroId) return;

    showToast(`Executing macro on ${checked.length} profiles...`, 'info');
    closeRunMacroModal();

    try {
        const res = await fetch(`${API}/api/macros/run/bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: checked, macro_id: macroId })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
        } else {
            showToast('Error: ' + data.detail, 'error');
        }
    } catch(e) {
        showToast('Failed to start macro', 'error');
    }
}

// =========================================================
// AUTOMATION (MACROS & SCHEDULES)
// =========================================================
function switchAutomationTab(tabName) {
    const automation = document.getElementById('page-automation');
    automation.querySelectorAll('.page-toolbar .modal-tab').forEach(t => t.classList.remove('active'));
    const tabButton = automation.querySelector(`.page-toolbar .modal-tab[data-tab="${tabName}"]`);
    if (tabButton) tabButton.classList.add('active');

    automation.querySelectorAll('[id^="auto-tab-"]').forEach(c => c.classList.remove('active'));
    const panel = document.getElementById(`auto-tab-${tabName}`);
    if (panel) panel.classList.add('active');

    ['macros', 'schedules', 'cookie-robot', 'sync'].forEach(name => {
        const actions = document.getElementById(`automation-${name}-actions`);
        if (actions) actions.style.display = name === tabName ? 'block' : 'none';
    });

    if (tabName === 'macros') fetchMacros();
    if (tabName === 'schedules') fetchSchedules();
    if (tabName === 'cookie-robot') fetchCookieRobotStatus();
    updateAutomationSelectionCounts();
}

async function fetchMacros() {
    try {
        const data = await requestJson(`${API}/api/macros`, {}, 'Could not load macros');
        currentMacros = Array.isArray(data) ? data : [];

        const grid = document.getElementById('macros-grid');
        grid.innerHTML = '';

        if (currentMacros.length === 0) {
            grid.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:2rem;color:var(--text-muted);">No macros found. Create one!</td></tr>`;
            return;
        }

        currentMacros.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${escHtml(m.name)}</strong></td>
                <td>${escHtml(m.description) || '-'}</td>
                <td><span class="mono" style="background:rgba(255,255,255,0.1);padding:0.2rem 0.5rem;border-radius:4px;">${Array.isArray(m.steps) ? m.steps.length : 0} steps</span></td>
                <td>
                    <button class="btn-secondary" style="padding:0.25rem 0.5rem;font-size:0.8rem;border-color:var(--danger);color:var(--danger);" data-action="delete-macro" data-macro-id="${escAttr(m.id)}">Delete</button>
                </td>
            `;
            grid.appendChild(tr);
        });
    } catch(e) { console.error('Failed to fetch macros:', e); }
}

async function fetchSchedules() {
    try {
        const data = await requestJson(`${API}/api/macros/schedule`, {}, 'Could not load schedules');
        currentSchedules = Array.isArray(data) ? data : [];

        const grid = document.getElementById('schedules-grid');
        grid.innerHTML = '';

        if (currentSchedules.length === 0) {
            grid.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--text-muted);">No active cron schedules.</td></tr>`;
            return;
        }

        currentSchedules.forEach(s => {
            const profileIds = Array.isArray(s.profile_ids) ? s.profile_ids : [];
            const macro = currentMacros.find(m => m.id === s.macro_id);
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><span class="mono" style="background:rgba(255,255,255,0.1);padding:0.2rem 0.5rem;border-radius:4px;">${escHtml(s.cron)}</span></td>
                <td>${escHtml(macro?.name || s.macro_id)}</td>
                <td>${profileIds.includes('*') ? 'All Profiles' : profileIds.length + ' Profiles'}</td>
                <td><span style="color:var(--success);">Active</span></td>
                <td>
                    <button class="btn-secondary" style="padding:0.25rem 0.5rem;font-size:0.8rem;border-color:var(--danger);color:var(--danger);" data-action="delete-schedule" data-schedule-id="${escAttr(s.id)}">Stop</button>
                </td>
            `;
            grid.appendChild(tr);
        });
    } catch(e) { console.error('Failed to fetch schedules:', e); }
}

function openMacroModal() {
    document.getElementById('new-macro-name').value = '';
    document.getElementById('new-macro-desc').value = '';
    document.getElementById('macro-steps-container').innerHTML = '';
    addMacroStep(); // start with one empty step
    document.getElementById('macro-modal').classList.add('show');
}

function closeMacroModal() {
    document.getElementById('macro-modal').classList.remove('show');
}

function updateMacroStepFields(selectEl) {
    const step = selectEl.closest('.macro-step');
    const valInput = step.querySelector('.step-value');
    if (['type', 'wait'].includes(selectEl.value)) {
        valInput.style.display = 'block';
    } else {
        valInput.style.display = 'none';
        valInput.value = '';
    }
}

function addMacroStep() {
    const tpl = document.getElementById('tpl-macro-step');
    const clone = tpl.content.cloneNode(true);
    document.getElementById('macro-steps-container').appendChild(clone);
}

async function submitCreateMacro() {
    const name = document.getElementById('new-macro-name').value;
    const desc = document.getElementById('new-macro-desc').value;

    if (!name) {
        showToast('Macro name is required', 'error');
        return;
    }

    const stepEls = document.querySelectorAll('.macro-step');
    const steps = Array.from(stepEls).map(el => {
        return {
            action: el.querySelector('.step-action').value,
            selector: el.querySelector('.step-selector').value,
            value: el.querySelector('.step-value').value
        };
    }).filter(s => s.selector || s.action === 'wait'); // filter out totally empty steps

    if (steps.length === 0) {
        showToast('Add at least one step', 'error');
        return;
    }

    try {
        const res = await fetch(`${API}/api/macros`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc, steps })
        });
        if (res.ok) {
            showToast('Macro saved successfully!', 'success');
            closeMacroModal();
            fetchMacros();
        } else {
            const data = await res.json();
            showToast('Error: ' + data.detail, 'error');
        }
    } catch(e) { showToast('Error saving macro', 'error'); }
}

async function deleteMacro(id) {
    if (!confirm('Delete this macro?')) return;
    try {
        const data = await requestJson(`${API}/api/macros/${id}`, { method: 'DELETE' }, 'Macro deletion failed');
        if (data.status && data.status !== 'success') throw new Error(data.message || 'Macro deletion failed');
        showToast('Macro deleted', 'info');
        fetchMacros();
    } catch(e) { showToast(e.message, 'error'); }
}

async function openScheduleModal() {
    // Fetch macros for dropdown
    try {
        const macros = await requestJson(`${API}/api/macros`, {}, 'Could not load macros');
        currentMacros = Array.isArray(macros) ? macros : [];
        const mSelect = document.getElementById('schedule-macro-select');
        mSelect.innerHTML = currentMacros.map(m => `<option value="${escAttr(m.id)}">${escHtml(m.name)}</option>`).join('');
    } catch(e) {
        showToast(e.message, 'error');
        return;
    }

    // Populate profiles
    const pContainer = document.getElementById('schedule-profiles-list');
    pContainer.innerHTML = `
        <label style="display:flex;align-items:center;gap:0.5rem;padding:0.25rem 0;cursor:pointer;">
            <input type="checkbox" value="*" id="schedule-all-profiles">
            <strong>* (All Existing & Future Profiles)</strong>
        </label>
    `;

    pContainer.insertAdjacentHTML('beforeend', allProfiles.map(p => `
            <label style="display:flex;align-items:center;gap:0.5rem;padding:0.25rem 0;cursor:pointer;">
                <input type="checkbox" class="schedule-profile-cb" value="${escAttr(p.id)}">
                ${escHtml(p.name)}
            </label>
        `).join(''));

    document.getElementById('schedule-cron').value = '0 * * * *'; // default hourly
    document.getElementById('schedule-modal').classList.add('show');
}

function closeScheduleModal() {
    document.getElementById('schedule-modal').classList.remove('show');
}

async function submitCreateSchedule() {
    const macro_id = document.getElementById('schedule-macro-select').value;
    const cron = document.getElementById('schedule-cron').value;

    const isAll = document.getElementById('schedule-all-profiles').checked;
    let profile_ids = [];
    if (isAll) {
        profile_ids = ['*'];
    } else {
        document.querySelectorAll('.schedule-profile-cb:checked').forEach(cb => {
            profile_ids.push(cb.value);
        });
    }

    if (!macro_id) { showToast('Please select a macro', 'error'); return; }
    if (!cron) { showToast('Cron expression required', 'error'); return; }
    if (profile_ids.length === 0) { showToast('Select at least one profile', 'error'); return; }

    try {
        await requestJson(`${API}/api/macros/schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ macro_id, profile_ids, cron })
        }, 'Schedule creation failed');
        showToast('Schedule created successfully!', 'success');
        closeScheduleModal();
        fetchSchedules();
    } catch(e) { showToast('Error creating schedule', 'error'); }
}

async function deleteSchedule(jobId) {
    if (!confirm('Stop this cron job?')) return;
    try {
        await requestJson(`${API}/api/macros/schedule/${jobId}`, { method: 'DELETE' }, 'Schedule deletion failed');
        showToast('Schedule stopped', 'info');
        fetchSchedules();
    } catch(e) { showToast(e.message, 'error'); }
}

// =========================================================
// UTILS
// =========================================================
function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g, '&#39;');
}

function escAttr(value) {
    return escHtml(String(value ?? ''));
}

function inlineStringArg(value) {
    return escAttr(JSON.stringify(String(value ?? '')).replace(/</g, '\\u003c').replace(/>/g, '\\u003e'));
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// =========================================================
// VIRTUAL KEYBOARD
// =========================================================
function toggleVirtualKeyboardSetting() {
    useVirtualKeyboard = !useVirtualKeyboard;
    localStorage.setItem('useVirtualKeyboard', useVirtualKeyboard ? 'true' : 'false');
    updateVirtualKeyboardToggleUI();
    if (!useVirtualKeyboard) closeVirtualKeyboard();
    showToast(useVirtualKeyboard ? 'Virtual keyboard enabled for sensitive fields' : 'Virtual keyboard disabled', 'info');
}

function updateVirtualKeyboardToggleUI() {
    const toggle = document.getElementById('toggle-use-virtual-keyboard');
    if (toggle) toggle.classList.toggle('active', useVirtualKeyboard);
}

function initVirtualKeyboard() {
    useVirtualKeyboard = localStorage.getItem('useVirtualKeyboard') === 'true';
    updateVirtualKeyboardToggleUI();

    const keyboard = document.getElementById('virtual-keyboard');
    if (!keyboard) return;

    keyboard.addEventListener('mousedown', (e) => {
        const key = e.target.closest('.virtual-key');
        if (key) {
            e.preventDefault();
            const value = key.dataset.key;
            if (value) handleVirtualKey(value);
        } else {
            // Clicking header or gaps must not steal focus from the input
            e.preventDefault();
        }
    });

    document.addEventListener('focusin', (e) => {
        if (useVirtualKeyboard && e.target.matches('[data-secure="true"]')) {
            openVirtualKeyboard(e.target);
        }
    });

    document.addEventListener('focusout', (e) => {
        if (vkCurrentInput && e.target === vkCurrentInput) {
            const related = e.relatedTarget;
            if (!related || !keyboard.contains(related)) {
                closeVirtualKeyboard();
            }
        }
    });

    document.addEventListener('keydown', (e) => {
        if (useVirtualKeyboard && e.target.matches('[data-secure="true"]')) {
            if (allowPhysicalSecureKey(e)) return;
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);
}

function allowPhysicalSecureKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return true;
    if (e.key === 'Tab' || e.key === 'Escape' || e.key === 'Enter' || e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') return true;
    return false;
}

function openVirtualKeyboard(inputField) {
    if (!inputField) return;
    vkCurrentInput = inputField;
    const keyboard = document.getElementById('virtual-keyboard');
    if (!keyboard) return;
    keyboard.style.display = 'flex';
    renderVirtualKeyboard();
    // Position after layout is computed
    requestAnimationFrame(() => {
        positionVirtualKeyboard();
        inputField.focus({ preventScroll: true });
    });
}

function closeVirtualKeyboard() {
    const keyboard = document.getElementById('virtual-keyboard');
    if (keyboard) {
        keyboard.style.display = 'none';
        keyboard.innerHTML = '';
    }
    vkCurrentInput = null;
    vkShift = false;
    vkCaps = false;
}

function positionVirtualKeyboard() {
    const keyboard = document.getElementById('virtual-keyboard');
    const input = vkCurrentInput;
    if (!keyboard || !input) return;
    const rect = input.getBoundingClientRect();
    const pad = 10;
    let top = rect.bottom + pad;
    let left = rect.left;
    const maxTop = window.innerHeight - keyboard.offsetHeight - pad;
    if (top > maxTop && rect.top - keyboard.offsetHeight - pad > 0) {
        top = rect.top - keyboard.offsetHeight - pad;
    }
    if (top < pad) top = pad;
    const maxLeft = window.innerWidth - keyboard.offsetWidth - pad;
    if (left > maxLeft) left = maxLeft;
    if (left < pad) left = pad;
    keyboard.style.top = `${top}px`;
    keyboard.style.left = `${left}px`;
}

function getVirtualKeyboardLayout() {
    let chars = [];
    if (vkScrambled) {
        chars = '1234567890qwertyuiopasdfghjklzxcvbnm'.split('');
        for (let i = chars.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [chars[i], chars[j]] = [chars[j], chars[i]];
        }
    } else {
        chars = '1234567890qwertyuiopasdfghjklzxcvbnm'.split('');
    }
    const lengths = [10, 10, 9, 7];
    const rows = [];
    let idx = 0;
    for (const len of lengths) {
        rows.push(chars.slice(idx, idx + len));
        idx += len;
    }
    return rows;
}

function renderVirtualKeyboard() {
    const keyboard = document.getElementById('virtual-keyboard');
    if (!keyboard) return;

    const rows = getVirtualKeyboardLayout();
    let html = '<div class="vk-overlay">Secure input — click keys below</div>';

    rows.forEach((row, rIndex) => {
        html += '<div class="vk-row">';
        row.forEach(ch => {
            const display = getVirtualKeyDisplay(ch);
            html += `<button type="button" class="virtual-key" data-key="${escAttr(ch)}" aria-label="${escAttr(display)}">${escHtml(display)}</button>`;
        });
        // Backspace on the last character row
        if (rIndex === rows.length - 1) {
            html += '<button type="button" class="virtual-key wide function" data-key="Backspace" aria-label="Backspace">⌫</button>';
        }
        html += '</div>';
    });

    html += '<div class="vk-row">';
    html += `<button type="button" class="virtual-key function ${vkShift ? 'active' : ''}" data-key="Shift" aria-label="Shift">Shift</button>`;
    html += `<button type="button" class="virtual-key function ${vkCaps ? 'active' : ''}" data-key="CapsLock" aria-label="Caps Lock">Caps</button>`;
    html += '<button type="button" class="virtual-key wide function" data-key="Space" aria-label="Space">Space</button>';
    html += '<button type="button" class="virtual-key function" data-key="Clear" aria-label="Clear">Clear</button>';
    html += `<button type="button" class="virtual-key function ${vkScrambled ? 'active' : ''}" data-key="Scramble" aria-label="Scramble layout">🔀</button>`;
    html += '<button type="button" class="virtual-key function" data-key="Close" aria-label="Close keyboard">Close</button>';
    html += '</div>';

    keyboard.innerHTML = html;
}

function getVirtualKeyDisplay(ch) {
    if (/^[a-z]$/.test(ch) && (vkShift !== vkCaps)) {
        return ch.toUpperCase();
    }
    return ch;
}

function handleVirtualKey(key) {
    if (key === 'Close') {
        closeVirtualKeyboard();
        return;
    }
    if (key === 'Shift') {
        vkShift = !vkShift;
        renderVirtualKeyboard();
        return;
    }
    if (key === 'CapsLock') {
        vkCaps = !vkCaps;
        renderVirtualKeyboard();
        return;
    }
    if (key === 'Scramble') {
        vkScrambled = !vkScrambled;
        renderVirtualKeyboard();
        return;
    }

    if (!vkCurrentInput) return;

    if (key === 'Backspace') {
        virtualBackspace();
    } else if (key === 'Space') {
        virtualInsert(' ');
    } else if (key === 'Clear') {
        vkCurrentInput.value = '';
        vkCurrentInput.dispatchEvent(new Event('input', { bubbles: true }));
        vkCurrentInput.focus({ preventScroll: true });
    } else {
        virtualInsert(getVirtualKeyDisplay(key));
    }

    // Shift is a one-shot modifier for letters
    if (vkShift) {
        vkShift = false;
        renderVirtualKeyboard();
    }
}

function virtualInsert(text) {
    const input = vkCurrentInput;
    if (!input) return;
    const start = input.selectionStart || 0;
    const end = input.selectionEnd || 0;
    input.setRangeText(text, start, end, 'end');
    input.focus({ preventScroll: true });
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

function virtualBackspace() {
    const input = vkCurrentInput;
    if (!input) return;
    const start = input.selectionStart || 0;
    const end = input.selectionEnd || 0;
    if (start === end && start > 0) {
        input.setRangeText('', start - 1, start, 'end');
    } else if (start !== end) {
        input.setRangeText('', start, end, 'end');
    }
    input.focus({ preventScroll: true });
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

// =========================================================
// AUTO REFRESH
// =========================================================
function startPolling() {
    fetchMetrics();
    fetchCFStatus();

    // Refresh every 5 seconds
    setInterval(() => {
        fetchMetrics();
        fetchCFStatus();

        // Only refresh profiles if on that page
        const profilesPage = document.getElementById('page-profiles');
        if (profilesPage && profilesPage.classList.contains('active')) {
            fetchProfiles();
        }

        const proxiesPage = document.getElementById('page-proxies');
        if (proxiesPage && proxiesPage.classList.contains('active')) {
            fetchTitanProxies();
            fetchProxies();
        }
    }, 5000);
}

// =========================================================
// INIT
// =========================================================
document.addEventListener('DOMContentLoaded', () => {
    // Dynamic rows cannot use inline event attributes: the strict CSP permits
    // scripts from this application but intentionally blocks unsafe-inline.
    document.addEventListener('click', (event) => {
        const control = event.target.closest('[data-action]');
        if (!control) return;
        const { action, profileId, macroId, scheduleId, pinned } = control.dataset;
        const actions = {
            'toggle-profile-pin': () => toggleProfilePin(profileId, pinned === 'true'),
            'open-edit-modal': () => openEditModal(profileId),
            'stop-profile': () => stopProfile(profileId),
            'launch-profile': () => launchProfile(profileId),
            'scan-profile': () => scanProfile(profileId),
            'open-metadata-modal': () => openMetadataModal(profileId),
            'tag-profile': () => tagProfilePrompt(profileId),
            'clone-profile': () => cloneProfile(profileId),
            'open-cookie-modal': () => openCookieModal(profileId),
            'open-set-pin-modal': () => openSetPinModal(profileId),
            'delete-profile': () => deleteProfile(profileId),
            'delete-macro': () => deleteMacro(macroId),
            'delete-schedule': () => deleteSchedule(scheduleId),
        };
        if (actions[action]) actions[action]();
    });
    document.addEventListener('change', (event) => {
        const control = event.target.closest('[data-action]');
        if (!control) return;
        if (control.dataset.action === 'update-bulk-actions') updateBulkActions();
        if (control.dataset.action === 'set-privacy-mode') setPrivacyMode(control.dataset.profileId, control.value);
    });
    // Bind navigation in JavaScript as well as retaining the markup fallback.
    // This keeps sidebar controls functional in packaged and hardened runtimes
    // where inline event handlers may be disabled.
    document.querySelectorAll('.nav-item[data-page]').forEach((item) => {
        item.addEventListener('click', (event) => {
            event.preventDefault();
            navigate(item.dataset.page);
        });
        item.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigate(item.dataset.page);
            }
        });
    });

    navigate('dashboard');
    startPolling();
    addLogEntry('info', 'GhostBrowser dashboard initialized');
    addLogEntry('info', 'Primary UI generation is configured for Cloudflare Kimi AI');

    // Load auto-replenish state
    const autoReplenishToggle = document.getElementById('auto-replenish-toggle');
    if (autoReplenishToggle) {
        autoReplenishToggle.checked = localStorage.getItem('auto_replenish') === 'true';
    }

    // Initialize virtual keyboard for secure inputs
    initVirtualKeyboard();

    // Ensure the CSRF/XSRF token is available for state-changing requests.
    ensureXsrfToken();

    // Prompt for the admin token if the backend is protected.
    ensureAdminToken();

    // Close modal on overlay click
    document.getElementById('create-modal').addEventListener('click', function(e) {
        if (e.target === this) closeCreateModal();
    });
});

function toggleAutoReplenish() {
    const isChecked = document.getElementById('auto-replenish-toggle').checked;
    localStorage.setItem('auto_replenish', isChecked);
    showToast(isChecked ? 'Auto-Replenish Enabled' : 'Auto-Replenish Disabled', isChecked ? 'success' : 'info');
}

// =========================================================
// THEME TOGGLE
// =========================================================
function toggleTheme() {
    const root = document.documentElement;
    const current = root.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    const btn = document.querySelector('.theme-toggle');
    if (btn) btn.textContent = next === 'dark' ? '🌙' : '☀️';
}

// Load saved theme on startup
(function() {
    const saved = localStorage.getItem('theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        const btn = document.querySelector('.theme-toggle');
        if (btn) btn.textContent = '☀️';
    }
})();

// =========================================================
// COOKIE ROBOT
// =========================================================
function getSelectedProfileIds() {
    return Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
}

function updateAutomationSelectionCounts() {
    const selected = getSelectedProfileIds().length;
    const cookieCount = document.getElementById('cookie-robot-profile-count');
    if (cookieCount) cookieCount.textContent = `${selected} selected`;
}

async function startCookieRobotWarming() {
    await startCookieRobot(getSelectedProfileIds());
}

async function startCookieRobot(profileIds) {
    if (!profileIds || profileIds.length === 0) {
        showToast('Select at least one profile', 'error');
        return;
    }
    try {
        const data = await requestJson(`${API}/api/cookie-robot/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        }, 'Cookie Robot could not start');
        showToast(data.message || 'Cookie Robot started!', 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function fetchCookieRobotStatus() {
    try {
        const res = await fetch(`${API}/api/cookie-robot/status`);
        if (!res.ok) return;
        const data = await res.json();
        // Update UI if widget exists
        const widget = document.getElementById('cookie-robot-widget');
        if (widget && data && Object.keys(data).length > 0) {
            widget.innerHTML = Object.entries(data).map(([pid, status]) => {
                const pct = Math.max(0, Math.min(100, Number(status.sites_visited) / Number(status.sites_total) * 100 || 0));
                return `<div class="cookie-robot-widget">
                    <span>${escHtml(pid.slice(0,8))}: ${escHtml(status.state)}</span>
                    <div class="warming-progress"><div class="warming-progress-bar" style="width:${pct}%"></div></div>
                </div>`;
            }).join('');
        }
    } catch (e) { /* backend not running */ }
}

// =========================================================
// SYNC (Synchronizer)
// =========================================================
async function startSyncSessionWrapper() {
    const profileIds = getSelectedProfileIds();
    if (profileIds.length === 0) {
        showToast('Select at least one profile on the Profiles page', 'error');
        return;
    }
    await startSyncSession(profileIds);
}

function setSyncUi(active, count = 0) {
    const indicator = document.getElementById('sync-indicator');
    const status = document.getElementById('sync-status-text');
    const total = document.getElementById('sync-profile-count');
    if (indicator) indicator.classList.toggle('active', active);
    if (status) status.textContent = active ? 'Sync Active' : 'Sync Inactive';
    if (total) total.textContent = `${count} profile${count === 1 ? '' : 's'} in sync`;
}

async function startSyncSession(profileIds) {
    try {
        const data = await requestJson(`${API}/api/sync/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        }, 'Synchronizer could not start');
        setSyncUi(true, profileIds.length);
        showToast(data.message || `Sync started for ${profileIds.length} profiles`, 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function stopSyncSession() {
    try {
        await requestJson(`${API}/api/sync/stop`, { method: 'POST' }, 'Synchronizer could not stop');
        setSyncUi(false, 0);
        showToast('Sync stopped', 'info');
    } catch (e) { showToast(e.message, 'error'); }
}

async function fetchDetectionRisk(profileId) {
    try {
        const res = await fetch(`${API}/api/profiles/${profileId}/detection-risk`);
        if (!res.ok) return null;
        return await res.json();
    } catch (e) { return null; }
}

async function applyRiskBadges(profiles) {
    for (const p of profiles) {
        const id = String(p.id || '');
        if (!id) continue;
        const nameEl = document.querySelector(`#card-${escAttr(id)} .td-name > div:last-child`);
        if (!nameEl) continue;
        const data = await fetchDetectionRisk(id);
        if (!data) continue;
        const score = data.risk_score || 0;
        const existing = nameEl.querySelector('.risk-badge');
        if (existing) existing.remove();
        if (score === 0) continue;
        const cls = score >= 70 ? 'risk-badge-high' : (score >= 40 ? 'risk-badge-med' : 'risk-badge-low');
        const title = (data.factors || []).join('; ') || 'Detection risk';
        const badge = document.createElement('span');
        badge.className = `risk-badge ${cls}`;
        badge.title = title;
        badge.textContent = score;
        nameEl.appendChild(badge);
    }
}

async function openSurfacesModal() {
    const modal = document.getElementById('surfaces-modal');
    if (modal) modal.style.display = 'flex';
    const tbody = document.getElementById('surfaces-table-body');
    const scoreEl = document.getElementById('surfaces-score');
    if (!tbody) return;
    try {
        const res = await fetch(`${API}/api/anti-detect/surfaces`);
        const json = res.ok ? await res.json() : { surfaces: [], score: {} };
        const score = json.score || {};
        const scoreText = `Protection score: ${score.score_percent !== undefined ? score.score_percent : '--'}% (${score.protected || 0}/${score.total || 0} protected)`;
        if (scoreEl) scoreEl.textContent = scoreText;
        const surfaces = Array.isArray(json.surfaces) ? json.surfaces : [];
        tbody.innerHTML = surfaces.map(s => {
            const statusClass = s.status === 'protected' ? 'status-success' : (s.status === 'partial' ? 'status-warning' : 'status-danger');
            const statusLabel = s.status === 'protected' ? 'protected' : (s.status === 'partial' ? 'partial' : 'not protected');
            return `<tr style="border-bottom:1px solid var(--border);">
                <td style="padding:0.4rem 0;" title="${escAttr(s.notes || '')}">${escHtml(s.name)}</td>
                <td style="padding:0.4rem 0;">${escHtml(s.category)}</td>
                <td style="padding:0.4rem 0;"><span class="${statusClass}" style="padding:2px 6px;border-radius:99px;font-size:0.7rem;font-weight:600;">${statusLabel}</span></td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3">Could not load surface inventory: ${escHtml(e.message)}</td></tr>`;
    }
}

function closeSurfacesModal() {
    const modal = document.getElementById('surfaces-modal');
    if (modal) modal.style.display = 'none';
}

// Close modal on outside click
window.addEventListener('click', function(e) {
    const modal = document.getElementById('surfaces-modal');
    if (modal && e.target === modal) closeSurfacesModal();
});

setInterval(() => {
    fetchCookieRobotStatus();
}, 5000);

// =========================================================
// ACCESS LOG
// =========================================================
async function openAccessLogModal() {
    const modal = document.getElementById('access-log-modal');
    if (modal) modal.style.display = 'flex';
    try {
        const data = await requestJson(`${API}/api/sites/access-log`, {}, 'Could not load access log');
        renderAccessLog(data);
    } catch (e) {
        renderAccessLog(null, e.message);
    }
}

function closeAccessLogModal() {
    const modal = document.getElementById('access-log-modal');
    if (modal) modal.style.display = 'none';
}

async function clearAccessLog() {
    if (!confirm('Clear the per-site API access log?')) return;
    try {
        await requestJson(`${API}/api/sites/access-log`, { method: 'DELETE' }, 'Could not clear access log');
        showToast('Access log cleared', 'success');
        renderAccessLog({ sites: {} });
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function formatApiList(site) {
    if (!Array.isArray(site) || site.length === 0) return '-';
    const apis = [...new Set(site.map(entry => String(entry.api || 'unknown')))];
    return apis.join(', ');
}

function renderAccessLog(data, errorMessage) {
    const tbody = document.getElementById('access-log-table-body');
    if (!tbody) return;
    if (errorMessage) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--danger);">Could not load access log: ${escHtml(errorMessage)}</td></tr>`;
        return;
    }
    const sites = data && typeof data.sites === 'object' ? data.sites : {};
    const entries = Object.entries(sites);
    if (entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);">No tracked API calls yet.</td></tr>';
        return;
    }
    const rows = entries.map(([origin, calls]) => {
        const list = Array.isArray(calls) ? calls : [];
        const lastEntry = list.length > 0
            ? list.slice().sort((a, b) => String(b.time).localeCompare(String(a.time)))[0]
            : null;
        const lastTime = lastEntry ? lastEntry.time : '-';
        const count = list.length;
        return `
            <tr>
                <td class="mono">${escHtml(origin)}</td>
                <td>${escHtml(formatApiList(list))}</td>
                <td class="mono">${escHtml(lastTime)}</td>
                <td>${escHtml(count)}</td>
            </tr>
        `;
    });
    tbody.innerHTML = rows.join('');
}

// Close access log modal on outside click
window.addEventListener('click', function(e) {
    const modal = document.getElementById('access-log-modal');
    if (modal && e.target === modal) closeAccessLogModal();
});
