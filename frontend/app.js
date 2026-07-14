/* ===== GhostBrowser App.js â€” Full Frontend Logic ===== */

const API = '';  // Same origin â€” FastAPI serves frontend

// =========================================================
// STATE
// =========================================================
let allProfiles = [];
let logFilter = 'all';
let createdProfileId = null;
let activityLog = [];
let chipState = { canvas: true, webgl: true, audio: true };

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
        const res = await fetch(`${API}/api/metrics`);
        if (!res.ok) return;
        const data = await res.json();

        document.getElementById('stat-active').textContent = `${data.active_profiles} / ${data.total_profiles}`;
        document.getElementById('stat-quarantine').textContent = data.quarantined_profiles;
        document.getElementById('stat-ram').textContent = `${data.memory_usage_percent.toFixed(1)}%`;
        document.getElementById('topbar-ram').textContent = `${data.memory_usage_percent.toFixed(1)}%`;

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
    } catch (e) { /* backend not running */ }
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
                <div class="cf-status ${isCooling ? 'cooldown' : 'healthy'}">${isCooling ? 'â³ Cooldown' : 'âœ“ Healthy'}</div>
                <div class="cf-account-id">${acc.account_id}</div>
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
    } catch (e) { /* backend offline */ }
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

    const PROFILE_COLORS = ['#6366f1','#f43f5e','#f59e0b','#10b981','#3b82f6','#8b5cf6','#ec4899','#06b6d4','#84cc16','#f97316'];

    profiles = [...profiles].sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));

    grid.innerHTML = profiles.map(p => {
        const isRunning = p.status === 'Running';
        const initials = (p.name || 'P').slice(0, 2).toUpperCase();
        const os = p.advanced?.os || p.os || '?';
        const osEmoji = os === 'Mac' ? '\u{1F34E}' : '\u{1FA9F}';
        const proxy = p.proxy ? p.proxy : 'No Proxy';
        const idShort = p.id ? p.id.split('-')[0] : 'N/A';
        const tags = Array.isArray(p.tags) ? p.tags : (p.tags ? String(p.tags).split(',').map(s=>s.trim()).filter(Boolean) : []);
        const tagElements = tags.map(t => `<span class="status-indicator" style="background:var(--primary);color:var(--bg);font-weight:600;font-size:0.65rem;padding:2px 6px;border-radius:3px;">${escHtml(t)}</span>`).join('');
        const isPinned = p.pinned === true;
        const color = p.color || PROFILE_COLORS[Math.abs(p.name.charCodeAt(0)) % PROFILE_COLORS.length];
        const pinBtn = isPinned ? '\u{1F4CC}' : '';

        return `
            <tr id="card-${p.id}">
                <td><input type="checkbox" class="profile-checkbox" value="${p.id}" onchange="updateBulkActions()"></td>
                <td>
                    <div class="td-name">
                        <div class="profile-icon-wrapper" style="border-left: 3px solid ${color};">${initials}</div>
                        <div>
                            <div style="display: flex; align-items: center; gap: 4px;">
                                ${pinBtn} ${escHtml(p.name)}
                                <button class="btn-icon" style="padding: 2px; color: var(--text-muted); background: transparent; border: none; cursor: pointer;" onclick="openEditModal('${p.id}')" title="Edit Settings">
                                    \u2699\uFE0F
                                </button>
                            </div>
                            <div style="display: flex; gap: 4px; margin-top: 4px;">
                                <span class="td-id">${idShort}</span>
                            </div>
                        </div>
                    </div>
                </td>
                <td>
                    <div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;">
                        ${tagElements || '<span style="color:var(--text-muted);font-size:0.75rem;">\u2014</span>'}
                    </div>
                </td>
                <td>
                    <div class="status-indicator ${isRunning ? 'running' : 'stopped'}">
                        ${isRunning ? '<div class="status-dot"></div>' : ''}
                        ${isRunning ? 'Running' : 'Stopped'}
                    </div>
                </td>
                <td><span class="td-proxy">${escHtml(typeof proxy === 'object' ? (proxy.server || 'No Proxy') : proxy)}</span></td>
                <td>
                    <div class="td-os">${osEmoji} ${os}</div>
                </td>
                <td class="td-actions">
                    <button class="btn-icon" style="padding:2px;background:transparent;border:none;cursor:pointer;" onclick="togglePin('${p.id}')" title="${isPinned ? 'Unpin' : 'Pin'}">
                        ${isPinned ? '\u{1F4CC}' : '\u{1F4CD}'}
                    </button>
                    ${isRunning
                        ? `<button class="btn-secondary btn-sm" onclick="stopProfile('${p.id}')">\u23F9 Stop</button>`
                        : `<button class="btn-primary btn-sm" onclick="launchProfile('${p.id}')">\u25B6 Launch</button>`}
                    <button class="btn-secondary btn-sm" onclick="scanProfile('${p.id}')" title="Scan Fingerprint Risk" style="padding: 0.25rem 0.5rem; color: var(--primary);">\u{1F6E1}\uFE0F</button>
                    <button class="btn-secondary btn-sm" onclick="openFingerprintModal('${p.id}')" title="Edit Fingerprint" style="padding: 0.25rem 0.5rem;">&#128300;</button>
                    <button class="btn-secondary btn-sm" onclick="openMetadataModal('${p.id}')" title="Tags & Proxy Pin" style="padding: 0.25rem 0.5rem;">\u{1F3F7}\uFE0F</button>
                    <button class="btn-secondary btn-sm" onclick="cloneProfile('${p.id}')" title="Clone Profile" style="padding: 0.25rem 0.5rem;">\u{1F9EC}</button>
                    <button class="btn-secondary btn-sm" onclick="openCookieModal('${p.id}')" title="Manage Cookies" style="padding: 0.25rem 0.5rem;">\u{1F36A}</button>
                    <button class="btn-icon stop" onclick="deleteProfile('${p.id}')" title="Delete Profile">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

async function togglePin(id) {
    try {
        const res = await fetch(`${API}/api/profiles`);
        const data = await res.json();
        const p = data.find(x => x.id === id);
        if (!p) return;
        const newPinned = !p.pinned;
        await fetch(`${API}/api/profiles/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pinned: newPinned })
        });
        showToast(newPinned ? 'Profile pinned' : 'Profile unpinned', 'success');
        fetchProfiles();
    } catch(e) { showToast('Error toggling pin', 'error'); }
}

async function setProfileColor(id, color) {
    try {
        await fetch(`${API}/api/profiles/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ color: color })
        });
        fetchProfiles();
    } catch(e) {}
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
        if (!p) return;
        document.getElementById('edit-profile-name').value = p.name || '';
        document.getElementById('edit-profile-locale').value = p.locale || '';
        document.getElementById('edit-profile-timezone').value = p.timezone || '';
        
        let proxyStr = '';
        if (p.proxy) {
            proxyStr = p.proxy.server || '';
            if (p.proxy.username) proxyStr += `:${p.proxy.username}:${p.proxy.password}`;
        }
        document.getElementById('edit-profile-proxy').value = proxyStr;
        
        const adv = p.advanced || {};
        document.getElementById('edit-profile-webrtc').value = adv.webrtc_mode || 'altered';
        
        if (adv.canvas_noise !== false) document.getElementById('edit-chip-canvas').classList.add('active');
        else document.getElementById('edit-chip-canvas').classList.remove('active');
        if (adv.webgl_noise !== false) document.getElementById('edit-chip-webgl').classList.add('active');
        else document.getElementById('edit-chip-webgl').classList.remove('active');
        if (adv.audio_noise !== false) document.getElementById('edit-chip-audio').classList.add('active');
        else document.getElementById('edit-chip-audio').classList.remove('active');
        if (adv.headless === true) document.getElementById('edit-chip-headless').classList.add('active');
        else document.getElementById('edit-chip-headless').classList.remove('active');
        
        // Color picker
        const PROFILE_COLORS = ['#6366f1','#f43f5e','#f59e0b','#10b981','#3b82f6','#8b5cf6','#ec4899','#06b6d4','#84cc16','#f97316'];
        const colorContainer = document.getElementById('edit-color-picker');
        if (colorContainer) {
            colorContainer.innerHTML = PROFILE_COLORS.map(c => 
                `<div class="color-swatch${(p.color || '#6366f1') === c ? ' active' : ''}" style="width:28px;height:28px;border-radius:50%;background:${c};cursor:pointer;border:2px solid ${(p.color || '#6366f1') === c ? '#fff' : 'transparent'};" onclick="selectEditColor('${c}')"></div>`
            ).join('');
        }
        
        document.getElementById('edit-modal').classList.add('show');
    } catch(e) {
        showToast('Error loading profile: ' + e.message, 'error');
    }
}

function selectEditColor(color) {
    document.querySelectorAll('#edit-color-picker .color-swatch').forEach(s => {
        s.style.border = '2px solid transparent';
        s.classList.remove('active');
    });
    event.target.style.border = '2px solid #fff';
    event.target.classList.add('active');
    document.getElementById('edit-selected-color').value = color;
}

async function saveProfileEdits() {
    if (!currentEditProfileId) return;
    
    const name = document.getElementById('edit-profile-name').value.trim();
    if (!name) {
        showToast('Name is required', 'error');
        return;
    }
    
    const payload = {
        name: name,
        locale: document.getElementById('edit-profile-locale').value.trim() || null,
        timezone: document.getElementById('edit-profile-timezone').value.trim() || null,
        proxy_string: document.getElementById('edit-profile-proxy').value.trim() || null,
        advanced: {
            webrtc_mode: document.getElementById('edit-profile-webrtc').value,
            canvas_noise: document.getElementById('edit-chip-canvas').classList.contains('active'),
            webgl_noise: document.getElementById('edit-chip-webgl').classList.contains('active'),
            audio_noise: document.getElementById('edit-chip-audio').classList.contains('active'),
            headless: document.getElementById('edit-chip-headless').classList.contains('active')
        },
        color: document.getElementById('edit-selected-color') ? document.getElementById('edit-selected-color').value : undefined,
    };
    
    try {
        const res = await fetch(`${API}/api/profiles/${currentEditProfileId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            showToast('Profile Updated Successfully', 'success');
            closeEditModal();
            fetchProfiles();
        } else {
            showToast('Failed to update profile settings', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// --- METADATA (TAGS/PROXY PIN) ---
let currentMetadataProfileId = null;

async function openMetadataModal(id) {
    currentMetadataProfileId = id;
    document.getElementById('metadata-modal').classList.add('show');
    try {
        const res = await fetch(`${API}/api/profiles`);
        const data = await res.json();
        // HIGH-02 FIX: API returns flat array, not {profiles: [...]}. Use data.find() directly.
        const p = Array.isArray(data) ? data.find(x => x.id === id) : null;
        if (p) {
            const tags = Array.isArray(p.tags) ? p.tags.join(', ') : (p.tags || '');
            document.getElementById('profile-tags').value = tags;
            document.getElementById('proxy-pin').value = p.proxy_pin || '';
        }
    } catch(e) {}
}

function closeMetadataModal() {
    document.getElementById('metadata-modal').classList.remove('show');
    currentMetadataProfileId = null;
}

async function saveMetadata() {
    if(!currentMetadataProfileId) return;
    const tags = document.getElementById('profile-tags').value;
    const proxy_pin = document.getElementById('proxy-pin').value;
    
    try {
        await fetch(`${API}/api/profiles/${currentMetadataProfileId}/metadata`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ tags: tags.split(',').map(s=>s.trim()).filter(Boolean), proxy_pin: proxy_pin })
        });
        showToast('Profile updated', 'success');
        closeMetadataModal();
        fetchProfiles();
    } catch(e) {
        showToast('Failed to update metadata', 'error');
    }
}

// --- CLONE PROFILE ---
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
            const breakdown = document.getElementById('scan-breakdown');
            breakdown.textContent = '';
            if (issues.length > 0) {
                issues.forEach(i => {
                    const div = document.createElement('div');
                    div.style.color = 'var(--danger)';
                    div.textContent = '- ' + i;
                    breakdown.appendChild(div);
                });
            } else {
                const div = document.createElement('div');
                div.style.color = 'var(--primary)';
                div.textContent = 'No major issues detected.';
                breakdown.appendChild(div);
            }
            
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
}

async function bulkLaunch() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return;
    for (const id of checked) {
        launchProfile(id); // Doesn't wait, launches in parallel
    }
}

async function bulkDelete() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return;
    if (!confirm(`Are you sure you want to delete ${checked.length} profiles?`)) return;
    
    for (const id of checked) {
        const row = document.getElementById(`card-${id}`);
        if (row) row.style.display = 'none';
    }
    
    let deletedCount = 0;
    for (const id of checked) {
        try {
            await fetch(`${API}/api/profiles/${id}`, { method: 'DELETE' });
            deletedCount++;
        } catch (e) {}
    }
    fetchProfiles();

    const isAutoReplenish = localStorage.getItem('auto_replenish') === 'true';
    if (isAutoReplenish && deletedCount > 0) {
        showToast(`Auto-replenishing ${deletedCount} profile(s)...`, 'info');
        try {
            await fetch(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_name: "AutoReplenish", count: deletedCount })
            });
            fetchProfiles();
            showToast(`Auto-replenished ${deletedCount} profile(s).`, 'success');
        } catch (e) {}
    }
}

// Modal Tabs Logic
// HIGH-05 FIX: Accept 'el' parameter instead of relying on implicit global 'event' object.
// The implicit 'event' fails in strict mode and Firefox.
function switchModalTab(tabName, el) {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    if (el) el.classList.add('active');
    document.getElementById('tab-' + tabName).classList.add('active');
}

﻿function filterProfiles() {
    const q = document.getElementById('profile-search').value.toLowerCase();
    const filtered = allProfiles.filter(p => {
        const tags = Array.isArray(p.tags) ? p.tags : (p.tags ? String(p.tags).split(',') : []);
        return p.name.toLowerCase().includes(q) ||
            p.id.toLowerCase().includes(q) ||
            (p.advanced?.os || '').toLowerCase().includes(q) ||
            tags.some(t => t.toLowerCase().includes(q));
    });
    renderProfiles(filtered);
}

async function launchProfile(id) {
    addActivity(`Launching profile ${id.split('-')[0]}...`, 'info');
    try {
        const res = await fetch(`${API}/api/profiles/${id}/launch`, { method: 'POST' });
        const d = await res.json();
        
        if (!res.ok) {
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
        await fetch(`${API}/api/profiles/${id}/close`, { method: 'POST' });
        showToast('Profile stopped', 'warning');
        addActivity(`Profile ${id.split('-')[0]} stopped`, 'warning');
        fetchProfiles();
    } catch (e) { showToast('Stop error: ' + e.message, 'error'); }
}

async function deleteProfile(id) {
    if (!confirm('Delete this profile? All data will be permanently lost.')) return;
    
    const row = document.getElementById(`card-${id}`);
    if (row) row.style.display = 'none';

    let success = false;
    try {
        await fetch(`${API}/api/profiles/${id}`, { method: 'DELETE' });
        showToast('Profile deleted', 'warning');
        addActivity(`Profile ${id.split('-')[0]} deleted`, 'warning');
        fetchProfiles();
        success = true;
    } catch (e) { showToast('Delete error: ' + e.message, 'error'); }

    if (success && localStorage.getItem('auto_replenish') === 'true') {
        showToast('Auto-replenishing 1 profile...', 'info');
        try {
            await fetch(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base_name: "AutoReplenish", count: 1 })
            });
            fetchProfiles();
            showToast('Auto-replenished 1 profile.', 'success');
        } catch (e) {}
    }
}

// =========================================================
// CREATE PROFILE MODAL
// =========================================================
function openCreateModal() {
    document.getElementById('create-modal').classList.add('active');
    showModalSection('modal-form');
}

function closeCreateModal() {
    document.getElementById('create-modal').classList.remove('active');
    // Reset form
    setTimeout(() => {
        document.getElementById('new-profile-name').value = '';
        document.getElementById('new-profile-proxy').value = '';
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
        webrtc.value = 'altered';
    } else if (val === 'social') {
        canvasChip.classList.remove('active'); // some social flags canvas noise
        webglChip.classList.add('active');
        audioChip.classList.add('active');
        headlessChip.classList.remove('active');
        trackersChip.classList.remove('active');
        webrtc.value = 'altered';
    } else if (val === 'research') {
        canvasChip.classList.remove('active');
        webglChip.classList.remove('active');
        audioChip.classList.remove('active');
        headlessChip.classList.add('active');
        trackersChip.classList.add('active');
        webrtc.value = 'disabled';
    }
}

async function submitCreateProfile() {
    const name = document.getElementById('new-profile-name').value.trim();
    if (!name) {
        showToast('Please enter a profile name.', 'warning');
        return;
    }

    const proxyRaw = document.getElementById('new-profile-proxy').value.trim();
    const count = parseInt(document.getElementById('new-profile-count').value) || 1;

    const payload = {
        name: name,
        proxy_string: proxyRaw || null,
        advanced: {
            os: document.getElementById('new-profile-os').value,
            webrtc_mode: document.getElementById('new-profile-webrtc').value,
            canvas_noise: document.getElementById('chip-canvas').classList.contains('active'),
            webgl_noise: document.getElementById('chip-webgl').classList.contains('active'),
            audio_noise: document.getElementById('chip-audio').classList.contains('active'),
            headless: document.getElementById('chip-headless').classList.contains('active'),
            block_trackers: document.getElementById('chip-trackers') ? document.getElementById('chip-trackers').classList.contains('active') : false,
            cpu_cores: 8,
            memory_gb: 16,
            screen_resolution: '1920x1080'
        }
    };

    // Show progress
    showModalSection('modal-progress');
    resetProgressSteps();

    addLogLine(`Starting Zero-Leak profile creation (${count > 1 ? 'Bulk Mode: ' + count + ' profiles' : 'Single Mode'})...`);

    setStepActive(1);
    addLogLine('Calling Kimi AI via Cloudflare Workers...');

    let profile;
    try {
        let resPromise;
        if (count > 1) {
            // Bulk creation
            resPromise = fetch(`${API}/api/profiles/generate/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    base_name: name,
                    count: count,
                    proxy: proxyRaw ? { server: proxyRaw } : null,
                    advanced: payload.advanced
                })
            });
        } else {
            // Single creation
            resPromise = fetch(`${API}/api/profiles/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }

        // --- SIMULATED DYNAMIC LOGGING FOR KIMI AI ---
        let isDone = false;
        
        const kimiLogs = [
            "Initializing Cloudflare Worker connection...",
            "Authenticating via proxy endpoint...",
            "Kimi AI generating WebGL vendor signature...",
            "Spoofing Canvas noise seed...",
            "Matching hardware concurrency to device memory...",
            "Running Coherence check across headers...",
            "Validating WebRTC leak protection...",
            "Bypassing CreepJS heuristic detection...",
            "Bypassing Sannysoft bot tests...",
            "AI verification in progress...",
            "Finalizing profile data structure..."
        ];
        
        const logSimulator = async () => {
            let stepIndex = 0;
            while (!isDone) {
                if (stepIndex < kimiLogs.length) {
                    addLogLine(`[Kimi AI] ${kimiLogs[stepIndex]}`);
                    
                    if (stepIndex === 3) {
                        setStepDone(1);
                        setStepActive(2);
                    } else if (stepIndex === 6) {
                        setStepDone(2);
                        setStepActive(3);
                    } else if (stepIndex === 8) {
                        setStepDone(3);
                        setStepActive(4);
                    }
                    
                    stepIndex++;
                } else {
                    addLogLine(`[Kimi AI] Waiting for final backend compilation...`);
                }
                await delay(1500 + Math.random() * 1000); // 1.5s - 2.5s between logs
            }
        };
        
        // Run simulator alongside the fetch
        logSimulator();

        // Wait for the actual request to complete
        const res = await resPromise;
        isDone = true; // Stop the simulator
        
        const data = await res.json();

        if (!res.ok) {
            setStepError(4);
            addLogLine(`âŒ Error: ${data.detail}`);
            document.getElementById('modal-error-msg').textContent = data.detail;
            showModalSection('modal-error');
            addActivity(`Profile creation failed: ${data.detail}`, 'error');
            addLogEntry('error', data.detail);
            return;
        }

        setStepDone(4);
        addLogLine('âœ… Profile accepted â€” Zero-Leak Ready!');
        profile = data;
        createdProfileId = profile.id || "bulk-creation";

    } catch (e) {
        setStepError(1);
        addLogLine(`âŒ Network error: ${e.message}`);
        document.getElementById('modal-error-msg').textContent = 'Could not reach backend: ' + e.message;
        showModalSection('modal-error');
        return;
    }

    // Show success
    await delay(400);
    const preview = document.getElementById('success-profile-preview');
    if (count > 1) {
        if (preview) {
            preview.textContent = '';
            const b = document.createElement('b'); b.textContent = 'Bulk Creation Complete';
            preview.appendChild(b); preview.appendChild(document.createElement('br'));
            preview.appendChild(document.createTextNode(profile.message || 'Multiple profiles created successfully.'));
            preview.appendChild(document.createElement('br'));
            preview.appendChild(document.createTextNode('Check the Profiles list to view them.'));
        }
        }
        addActivity(`Bulk created ${count} profiles`, 'success');
        addLogEntry('info', `Bulk profile creation finished.`);
    } else {
        if (preview) {
            preview.textContent = '';
            const b2 = document.createElement('b'); b2.textContent = 'Profile ID: '; preview.appendChild(b2);
            preview.appendChild(document.createTextNode(profile.id));
            preview.appendChild(document.createElement('br'));
                OS: ${profile.advanced?.os || profile.os || 'AI Generated'}<br>
                GPU: ${(profile.advanced?.webgl_renderer || profile.webgl_renderer || 'AI Generated').slice(0,50)}<br>
                Timezone: ${profile.timezone || 'AI Generated'}<br>
                Locale: ${profile.locale || 'AI Generated'}
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
    icon.textContent = 'âœ“';
}

function setStepError(n) {
    const step = document.getElementById(`step-${n}`);
    if (!step) return;
    step.className = 'progress-step error-step';
    const icon = step.querySelector('.step-icon');
    icon.className = 'step-icon error-icon';
    icon.textContent = 'âœ•';
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
                ${p.username ? `<span class="proxy-status alive">Auth</span>` : `<span class="proxy-status alive">Open</span>`}
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
                    <td class="mono">${p.ip}</td>
                    <td class="mono">${p.port}</td>
                    <td><span style="background:rgba(255,255,255,0.1);padding:2px 6px;border-radius:4px;font-size:0.75rem;">${p.protocol.toUpperCase()}</span></td>
                    <td>${p.city || 'Unknown'}, ${p.country || 'Unknown'}</td>
                    <td class="mono" style="color:${pingColor};">${p.latency_ms}ms</td>
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

function addLogEntry(level, msg) {
    const entry = { level, msg, time: new Date().toLocaleTimeString() };
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
        <div class="log-entry ${e.level}">
            <span class="log-ts">${e.time}</span>
            <span class="log-level">${e.level.toUpperCase()}</span>
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

    const item = document.createElement('div');
    item.className = 'activity-item';
    item.innerHTML = `
        <div class="activity-dot ${type}"></div>
        <span>${escHtml(msg)}</span>
        <span class="activity-time">${new Date().toLocaleTimeString()}</span>
    `;

    feed.insertBefore(item, feed.firstChild);

    // Keep max 20 items
    while (feed.children.length > 20) feed.removeChild(feed.lastChild);

    // Also log
    addLogEntry(type === 'success' ? 'info' : type === 'error' ? 'error' : type === 'warning' ? 'warning' : 'info', msg);
}

// =========================================================
// SETTINGS
// =========================================================
async function saveSettings() {
    const maxConcurrent = document.getElementById('setting-max-concurrent').value;
    try {
        await fetch(`${API}/api/rotator/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_concurrent: parseInt(maxConcurrent) })
        });
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
    toast.className = `toast ${type}`;

    const icons = { success: 'âœ…', error: 'âŒ', warning: 'âš ï¸', info: 'â„¹ï¸' };
    toast.innerHTML = `<span>${icons[type] || 'â„¹ï¸'}</span><span>${escHtml(msg)}</span>`;
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
let macroStepCount = 0;
let currentSchedules = [];

function openRunMacroModal() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) return showToast('Select at least one profile first', 'error');
    
    const select = document.getElementById('run-macro-select');
    select.innerHTML = currentMacros.map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');
    
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

async function openFingerprintModal(profileId) {
    document.getElementById('fingerprint-profile-id').value = profileId;
    try {
        const res = await fetch(`${API}/api/profiles/${profileId}/fingerprint`);
        if (!res.ok) throw new Error();
        const fp = await res.json();
        
        document.getElementById('fp-webgl-vendor').value = fp.webgl_vendor || '';
        document.getElementById('fp-webgl-renderer').value = fp.webgl_renderer || '';
        document.getElementById('fp-canvas-seed').value = fp.canvas_noise_seed || '';
        document.getElementById('fp-audio-noise').value = fp.audio_noise_seed || '';
        document.getElementById('fp-hardware-concurrency').value = fp.hardware_concurrency || 4;
        document.getElementById('fp-device-memory').value = fp.device_memory || 8;
        
        document.getElementById('fingerprint-modal').classList.add('show');
    } catch(e) {
        showToast('Error loading fingerprint', 'error');
    }
}

function closeFingerprintModal() {
    document.getElementById('fingerprint-modal').classList.remove('show');
}

async function saveFingerprint() {
    const id = document.getElementById('fingerprint-profile-id').value;
    const advanced = {
        webgl_vendor: document.getElementById('fp-webgl-vendor').value,
        webgl_renderer: document.getElementById('fp-webgl-renderer').value,
        canvas_noise_seed: document.getElementById('fp-canvas-seed').value,
        audio_noise_seed: document.getElementById('fp-audio-noise').value,
        hardware_concurrency: parseInt(document.getElementById('fp-hardware-concurrency').value) || 4,
        device_memory: parseInt(document.getElementById('fp-device-memory').value) || 8
    };
    
    try {
        const res = await fetch(`${API}/api/profiles/${id}/fingerprint`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ advanced })
        });
        if (res.ok) {
            showToast('Fingerprint spoof updated', 'success');
            closeFingerprintModal();
        } else {
            showToast('Failed to save fingerprint', 'error');
        }
    } catch(e) { showToast('Error saving fingerprint', 'error'); }
}

// =========================================================
// AUTOMATION (MACROS & SCHEDULES)
// =========================================================
function switchAutomationTab(tabName) {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.modal-tab[data-tab="${tabName}"]`).classList.add('active');
    
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(`auto-tab-${tabName}`).classList.add('active');
    
    document.getElementById('automation-macros-actions').style.display = tabName === 'macros' ? 'block' : 'none';
    document.getElementById('automation-schedules-actions').style.display = tabName === 'schedules' ? 'block' : 'none';
    
    if (tabName === 'macros') fetchMacros();
    if (tabName === 'schedules') fetchSchedules();
}

async function fetchMacros() {
    try {
        const res = await fetch(`${API}/api/macros`);
        if (!res.ok) return;
        const data = await res.json();
        
        const grid = document.getElementById('macros-grid');
        grid.innerHTML = '';
        
        if (data.length === 0) {
            grid.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:2rem;color:var(--text-muted);">No macros found. Create one!</td></tr>`;
            return;
        }
        
        data.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${escHtml(m.name)}</strong></td>
                <td>${escHtml(m.description) || '-'}</td>
                <td><span class="mono" style="background:rgba(255,255,255,0.1);padding:0.2rem 0.5rem;border-radius:4px;">${m.steps.length} steps</span></td>
                <td>
                    <button class="btn-secondary" style="padding:0.25rem 0.5rem;font-size:0.8rem;border-color:var(--danger);color:var(--danger);" onclick="deleteMacro('${m.id}')">Delete</button>
                </td>
            `;
            grid.appendChild(tr);
        });
    } catch(e) { console.error('Failed to fetch macros:', e); }
}

async function fetchSchedules() {
    try {
        const res = await fetch(`${API}/api/macros/schedule`);
        if (!res.ok) return;
        const data = await res.json();
        
        const grid = document.getElementById('schedules-grid');
        grid.innerHTML = '';
        
        if (data.length === 0) {
            grid.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:2rem;color:var(--text-muted);">No active cron schedules.</td></tr>`;
            return;
        }
        
        data.forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><span class="mono" style="background:rgba(255,255,255,0.1);padding:0.2rem 0.5rem;border-radius:4px;">${escHtml(s.cron_expression)}</span></td>
                <td>${escHtml(s.macro_id)}</td>
                <td>${s.profile_ids.includes('*') ? 'All Profiles' : s.profile_ids.length + ' Profiles'}</td>
                <td><span style="color:var(--success);">Active</span></td>
                <td>
                    <button class="btn-secondary" style="padding:0.25rem 0.5rem;font-size:0.8rem;border-color:var(--danger);color:var(--danger);" onclick="deleteSchedule('${s.job_id}')">Stop</button>
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
        await fetch(`${API}/api/macros/${id}`, { method: 'DELETE' });
        showToast('Macro deleted', 'info');
        fetchMacros();
    } catch(e) {}
}

async function openScheduleModal() {
    // Fetch macros for dropdown
    try {
        const mRes = await fetch(`${API}/api/macros`);
        const macros = await mRes.json();
        const mSelect = document.getElementById('schedule-macro-select');
        mSelect.innerHTML = macros.map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');
    } catch(e) {}
    
    // Populate profiles
    const pContainer = document.getElementById('schedule-profiles-list');
    pContainer.innerHTML = `
        <label style="display:flex;align-items:center;gap:0.5rem;padding:0.25rem 0;cursor:pointer;">
            <input type="checkbox" value="*" id="schedule-all-profiles"> 
            <strong>* (All Existing & Future Profiles)</strong>
        </label>
    `;
    
    allProfiles.forEach(p => {
        pContainer.innerHTML += `
            <label style="display:flex;align-items:center;gap:0.5rem;padding:0.25rem 0;cursor:pointer;">
                <input type="checkbox" class="schedule-profile-cb" value="${p.id}"> 
                ${escHtml(p.name)}
            </label>
        `;
    });
    
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
        const res = await fetch(`${API}/api/macros/schedule`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ macro_id, profile_ids, cron })
        });
        if (res.ok) {
            showToast('Schedule created successfully!', 'success');
            closeScheduleModal();
            fetchSchedules();
        } else {
            const data = await res.json();
            showToast('Error: ' + data.detail, 'error');
        }
    } catch(e) { showToast('Error creating schedule', 'error'); }
}

async function deleteSchedule(jobId) {
    if (!confirm('Stop this cron job?')) return;
    try {
        await fetch(`${API}/api/macros/schedule/${jobId}`, { method: 'DELETE' });
        showToast('Schedule stopped', 'info');
        fetchSchedules();
    } catch(e) {}
}

function startSyncSessionWrapper() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) { showToast('Select profiles to sync first', 'warning'); return; }
    startSyncSession(checked);
}
function startCookieRobotWarming() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (checked.length === 0) { showToast('Select profiles to warm first', 'warning'); return; }
    startCookieRobot(checked);
}

// =========================================================
// UTILS
// =========================================================
function escHtml(s) {
    if (typeof s !== 'string') return String(s || '');
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

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
    navigate('dashboard');
    startPolling();
    addLogEntry('info', 'GhostBrowser dashboard initialized');
    addLogEntry('info', 'Strict Kimi-only mode: ACTIVE â€” profiles require Cloudflare AI');

    // Load auto-replenish state
    const autoReplenishToggle = document.getElementById('auto-replenish-toggle');
    if (autoReplenishToggle) {
        autoReplenishToggle.checked = localStorage.getItem('auto_replenish') === 'true';
    }

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
    if (btn) btn.textContent = next === 'dark' ? 'ðŸŒ™' : 'â˜€ï¸';
}

// Load saved theme on startup
(function() {
    const saved = localStorage.getItem('theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        const btn = document.querySelector('.theme-toggle');
        if (btn) btn.textContent = 'â˜€ï¸';
    }
})();

// =========================================================
// FOLDERS
// =========================================================
async function fetchFolders() {
    try {
        const res = await fetch(`${API}/api/folders`);
        if (!res.ok) return;
        const data = await res.json();
        const folders = data.folders || data || [];
        // Update sidebar if it exists
        const sidebar = document.getElementById('folder-list');
        if (sidebar) {
            sidebar.innerHTML = '<div class="folder-item active" onclick="filterByFolder(null)">ðŸ“ All Profiles</div>' +
                folders.map(f => `<div class="folder-item" onclick="filterByFolder('${f.id}')">ðŸ“ ${f.name}</div>`).join('');
        }
    } catch (e) { /* backend not running */ }
}
﻿
let currentFolderFilter = null;

function filterByFolder(folderId) {
    currentFolderFilter = folderId;
    // Update active state in sidebar
    document.querySelectorAll('.folder-item').forEach(f => f.classList.remove('active'));
    if (!folderId) {
        document.querySelector('.folder-item').classList.add('active');
    } else {
        // Find and activate the matching folder item
        const items = document.querySelectorAll('.folder-item');
        items.forEach(item => {
            if (item.onclick && item.onclick.toString().includes(folderId)) {
                item.classList.add('active');
            }
        });
    }
    // Filter profiles
    if (!folderId) {
        renderProfiles(allProfiles);
    } else {
        const filtered = allProfiles.filter(p => p.folder_id === folderId);
        renderProfiles(filtered);
    }
}

// =========================================================
// COOKIE ROBOT
// =========================================================
async function startCookieRobot(profileIds) {
    if (!profileIds || profileIds.length === 0) {
        showToast('Select at least one profile', 'error');
        return;
    }
    try {
        const res = await fetch(`${API}/api/cookie-robot/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Cookie Robot started!', 'success');
        } else {
            showToast(data.message || 'Failed', 'error');
        }
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
                const pct = status.sites_visited / status.total_sites * 100 || 0;
                return `<div class="cookie-robot-widget">
                    <span>${pid.slice(0,8)}: ${status.status}</span>
                    <div class="warming-progress"><div class="warming-progress-bar" style="width:${pct}%"></div></div>
                </div>`;
            }).join('');
        }
    } catch (e) { /* backend not running */ }
}

// =========================================================
// SYNC (Synchronizer)
// =========================================================
async function startSyncSession(profileIds) {
    try {
        const res = await fetch(`${API}/api/sync/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        });
        const data = await res.json();
        if (res.ok) showToast(`Sync started for ${profileIds.length} profiles`, 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function stopSyncSession() {
    try {
        await fetch(`${API}/api/sync/stop`, { method: 'POST' });
        showToast('Sync stopped', 'info');
    } catch (e) { /* backend not running */ }
}

// =========================================================
// API KEYS
// =========================================================
async function fetchApiKeys() {
    try {
        const res = await fetch(`${API}/api/api-keys`);
        if (!res.ok) return;
        const data = await res.json();
        const keys = data.keys || data || [];
        const container = document.getElementById('api-keys-list');
        if (container) {
            if (keys.length === 0) {
                container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:1rem;">No API keys yet. Create one to enable external automation.</p>';
            } else {
                container.innerHTML = keys.map(k => `<div class="api-key-row">
                    <span class="api-key-value">${k.key || k.api_key || '?'}</span>
                    <span style="color:var(--text-muted);font-size:0.8rem;">${k.name || 'unnamed'}</span>
                    <button class="btn-secondary" onclick="revokeApiKey('${k.id}')">Revoke</button>
                </div>`).join('');
            }
        }
    } catch (e) { /* backend not running */ }
}

async function createApiKey(name) {
    try {
        const res = await fetch(`${API}/api/api-keys`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name || 'new-key' })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('API key created!', 'success');
            fetchApiKeys();
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function revokeApiKey(keyId) {
    try {
        await fetch(`${API}/api/api-keys/${keyId}`, { method: 'DELETE' });
        showToast('Key revoked', 'info');
        fetchApiKeys();
    } catch (e) { /* backend not running */ }
}

// =========================================================
// TEAM MANAGEMENT
// =========================================================
async function fetchTeamMembers() {
    try {
        const res = await fetch(`${API}/api/team/members`);
        if (!res.ok) return;
        const data = await res.json();
        const members = data.members || data || [];
        const container = document.getElementById('team-members-list');
        if (container) {
            container.innerHTML = members.map(m => `<div class="team-member-row">
                <span style="font-weight:600;">${m.name}</span>
                <span class="role-badge ${m.role}">${m.role}</span>
                <span style="color:var(--text-muted);font-size:0.8rem;">${m.email || ''}</span>
            </div>`).join('') || '<p style="color:var(--text-muted);text-align:center;padding:1rem;">No team members yet.</p>';
        }
    } catch (e) { /* backend not running */ }
}

// =========================================================
// RPA RECORDER
// =========================================================
async function startRpaRecording(profileId) {
    try {
        const res = await fetch(`${API}/api/rpa/record/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_id: profileId })
        });
        const data = await res.json();
        if (res.ok) showToast('Recording started', 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function stopRpaRecording() {
    try {
        const res = await fetch(`${API}/api/rpa/record/stop`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) showToast('Recorded ' + (data.steps || 0) + ' actions', 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function bulkLaunchProfiles(profileIds) {
    try {
        const res = await fetch(`${API}/api/profiles/bulk/launch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        });
        if (res.ok) showToast('Launching ' + profileIds.length + ' profiles...', 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function bulkCloseProfiles(profileIds) {
    try {
        const res = await fetch(`${API}/api/profiles/bulk/close`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds })
        });
        if (res.ok) showToast('Closing ' + profileIds.length + ' profiles...', 'success');
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function exportProfiles(profileIds) {
    try {
        const res = await fetch(`${API}/api/profiles/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_ids: profileIds || [] })
        });
        const data = await res.json();
        if (res.ok) {
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'ghostbrowser_profiles_export.json';
            a.click();
            URL.revokeObjectURL(url);
            showToast('Profiles exported!', 'success');
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

setInterval(() => {
    fetchCookieRobotStatus();
}, 5000);

// === IMPORT/EXPORT ===
async function exportProfile(id) {
    try {
        const res = await fetch(`${API}/api/profiles/${id}/export`);
        if (!res.ok) throw new Error('Export failed');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `profile_${id.substring(0,8)}.json`;
        a.click(); URL.revokeObjectURL(url);
        showToast('Profile exported', 'success');
    } catch(e) { showToast('Export error', 'error'); }
}

async function importProfiles() {
    const input = document.getElementById('import-file-input');
    if (!input || !input.files.length) return showToast('Select a JSON file', 'error');
    const formData = new FormData();
    formData.append('file', input.files[0]);
    try {
        const res = await fetch(`${API}/api/profiles/import`, { method: 'POST', body: formData });
        const data = await res.json();
        if (res.ok) { showToast(data.message || 'Imported!', 'success'); fetchProfiles(); }
        else showToast('Import failed', 'error');
    } catch(e) { showToast('Import error', 'error'); }
    input.value = '';
}

// === BULK ACTIONS ===
async function bulkAssignTag() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (!checked.length) return showToast('Select profiles first', 'error');
    const tag = prompt('Tag to add to ' + checked.length + ' profiles:');
    if (!tag || !tag.trim()) return;
    try {
        await fetch(`${API}/api/profiles/bulk/tag`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ profile_ids: checked, tags: [tag.trim()] })
        });
        showToast(`Tagged ${checked.length} profiles`, 'success'); fetchProfiles();
    } catch(e) { showToast('Error', 'error'); }
}

async function bulkMoveToFolder() {
    const checked = Array.from(document.querySelectorAll('.profile-checkbox:checked')).map(cb => cb.value);
    if (!checked.length) return showToast('Select profiles first', 'error');
    try {
        const res = await fetch(`${API}/api/folders`);
        const data = await res.json();
        const folders = data.folders || data || [];
        const name = prompt('Move ' + checked.length + ' profiles to folder:');
        if (!name || !name.trim()) return;
        const folder = folders.find(f => f.name.toLowerCase() === name.trim().toLowerCase());
        await fetch(`${API}/api/profiles/bulk/folder`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ profile_ids: checked, folder_id: folder ? folder.id : name.trim() })
        });
        showToast(`Moved ${checked.length} profiles`, 'success'); fetchProfiles();
    } catch(e) { showToast('Error', 'error'); }
}

// === FOLDER CREATION ===
async function createFolder() {
    const name = prompt('Folder name:');
    if (!name || !name.trim()) return;
    try {
        await fetch(`${API}/api/folders`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name: name.trim() })
        });
        showToast('Folder created', 'success'); fetchFolders();
    } catch(e) { showToast('Error', 'error'); }
}

// CF ACCOUNTS IMPORT
function showCFImportModal() {
    document.getElementById('cf-import-modal').style.display = 'flex';
    document.getElementById('cf-import-text').value = '';
    document.getElementById('cf-import-result').style.display = 'none';
    document.getElementById('cf-import-text').focus();
}

function closeCFImportModal() {
    document.getElementById('cf-import-modal').style.display = 'none';
}

async function importCFAccounts() {
    const text = document.getElementById('cf-import-text').value.trim();
    if (!text) { showToast('Paste account data first', 'error'); return; }

    const btn = document.getElementById('cf-import-btn');
    const result = document.getElementById('cf-import-result');
    btn.disabled = true;
    btn.textContent = 'Importing...';

    try {
        const res = await fetch(`${API}/api/cloudflare/import`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const data = await res.json();
        result.style.display = 'block';
        if (data.status === 'success') {
            result.style.color = 'var(--success)';
            result.textContent = `Imported ${data.imported} accounts. ${data.skipped} skipped. Total loaded: ${data.total_loaded}.`;
            fetchCFStatus();
        } else {
            result.style.color = 'var(--error)';
            result.textContent = data.message || 'Import failed.';
        }
    } catch(e) {
        result.style.display = 'block';
        result.style.color = 'var(--error)';
        result.textContent = 'Connection error: ' + e.message;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Import';
    }
}
