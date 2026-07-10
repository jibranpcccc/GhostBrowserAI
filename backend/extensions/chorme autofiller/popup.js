document.addEventListener('DOMContentLoaded', () => {

    // ─── DOM Refs ───
    const personaFields = document.querySelectorAll('.persona-field');
    const emailDomainInput = document.getElementById('emailDomain');
    const passwordLengthInput = document.getElementById('passwordLength');
    const bioSpintaxInput = document.getElementById('bioSpintax');
    const slowTypeInput = document.getElementById('slowType');
    const typeSpeedInput = document.getElementById('typeSpeed');
    const speedValueLabel = document.getElementById('speedValue');
    const speedSliderGroup = document.getElementById('speedSliderGroup');

    const mappingsContainer = document.getElementById('mappingsContainer');
    const addMappingBtn = document.getElementById('addMapping');
    
    const autoFillBtn = document.getElementById('autoFillBtn');
    const clearFormBtn = document.getElementById('clearFormBtn');
    const extractFormBtn = document.getElementById('extractFormBtn');
    const generatePersonaBtn = document.getElementById('generatePersonaBtn');
    const spinBioBtn = document.getElementById('spinBioBtn');

    // Profile refs
    const profileSelect = document.getElementById('profileSelect');
    const profileNameInput = document.getElementById('profileNameInput');
    const saveProfileBtn = document.getElementById('saveProfileBtn');
    const loadProfileBtn = document.getElementById('loadProfileBtn');
    const deleteProfileBtn = document.getElementById('deleteProfileBtn');
    const exportProfilesBtn = document.getElementById('exportProfilesBtn');
    const importProfilesBtn = document.getElementById('importProfilesBtn');
    const importFileInput = document.getElementById('importFileInput');

    // Theme ref
    const themeToggle = document.getElementById('themeToggle');
    const toast = document.getElementById('toast');

    let currentPersona = {};
    let currentConfig = { emailDomain: '', passwordLength: 12, bioSpintax: '', customMappings: [], slowType: false, typeSpeed: 30 };
    let savedProfiles = {}; // { "My Info": { persona: {...}, config: {...} }, ... }

    // ─── Toast Helper ───
    const showToast = (msg, type = '') => {
        toast.textContent = msg;
        toast.className = 'toast' + (type ? ` ${type}` : '');
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2200);
    };

    // ─── Theme ───
    const applyTheme = (theme) => {
        document.documentElement.setAttribute('data-theme', theme);
        themeToggle.textContent = theme === 'dark' ? '☀️' : '🌙';
    };

    chrome.storage.local.get(['theme'], (r) => {
        applyTheme(r.theme || 'light');
    });

    themeToggle.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        chrome.storage.local.set({ theme: next });
    });

    // ─── Copy Buttons ───
    const copyBtns = document.querySelectorAll('.copy-btn');
    copyBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = btn.getAttribute('data-target');
            const targetEl = document.getElementById(targetId);
            if (targetEl && targetEl.value) {
                navigator.clipboard.writeText(targetEl.value).then(() => {
                    const origText = btn.textContent;
                    btn.textContent = '✔️';
                    setTimeout(() => btn.textContent = origText, 1000);
                });
            }
        });
    });

    // ─── Speed Slider ───
    const slowStatusLabel = document.getElementById('slowStatusLabel');
    const updateSpeedUI = () => {
        if (slowTypeInput.checked) {
            speedSliderGroup.style.display = '';
            if (slowStatusLabel) {
                slowStatusLabel.textContent = 'ON';
                slowStatusLabel.style.color = 'var(--green)';
                slowStatusLabel.style.fontWeight = '700';
            }
        } else {
            speedSliderGroup.style.display = 'none';
            if (slowStatusLabel) {
                slowStatusLabel.textContent = 'OFF';
                slowStatusLabel.style.color = 'var(--text-muted)';
                slowStatusLabel.style.fontWeight = '600';
            }
        }
        speedValueLabel.textContent = typeSpeedInput.value + 'ms';
    };

    slowTypeInput.addEventListener('change', () => {
        updateSpeedUI();
        saveState();
    });

    typeSpeedInput.addEventListener('input', () => {
        speedValueLabel.textContent = typeSpeedInput.value + 'ms';
        saveState();
    });

    // ─── Initial Load ───
    chrome.storage.local.get(['activePersona', 'config', 'savedProfiles'], (result) => {
        // Load saved profiles
        if (result.savedProfiles) {
            savedProfiles = result.savedProfiles;
            refreshProfileDropdown();
        }

        if (result.config) {
            currentConfig = result.config;
            emailDomainInput.value = currentConfig.emailDomain || '';
            passwordLengthInput.value = currentConfig.passwordLength || 12;
            bioSpintaxInput.value = currentConfig.bioSpintax || '';
            slowTypeInput.checked = currentConfig.slowType || false;
            typeSpeedInput.value = currentConfig.typeSpeed || 30;
            
            if (currentConfig.customMappings && currentConfig.customMappings.length > 0) {
                currentConfig.customMappings.forEach(mapping => addMappingRow(mapping.selector, mapping.type));
            } else {
                addMappingRow('', 'Email');
            }
        } else {
            addMappingRow('', 'Email');
        }

        updateSpeedUI();

        if (result.activePersona && Object.keys(result.activePersona).length > 0) {
            currentPersona = result.activePersona;
            renderPersona();
        } else {
            rollNewPersona();
        }
    });

    // ─── Persona Rendering ───
    const renderPersona = () => {
        personaFields.forEach(field => {
            const key = field.getAttribute('data-key');
            if (currentPersona[key] !== undefined) {
                field.value = currentPersona[key];
            } else {
                field.value = '';
            }
        });
    };

    const rollNewPersona = () => {
        if (window.AutoFillUtils) {
            currentConfig = buildConfig();
            currentPersona = window.AutoFillUtils.generatePersona(currentConfig);
            renderPersona();
            saveState();
        }
    };

    personaFields.forEach(field => {
        field.addEventListener('input', (e) => {
            const key = e.target.getAttribute('data-key');
            currentPersona[key] = e.target.value;
            saveState();
        });
    });

    generatePersonaBtn.addEventListener('click', rollNewPersona);

    spinBioBtn.addEventListener('click', (e) => {
        e.preventDefault();
        saveState(); // grab latest spintax from text box
        if (currentConfig.bioSpintax && window.AutoFillUtils) {
            const newBio = window.AutoFillUtils.spinText(currentConfig.bioSpintax);
            currentPersona.Bio = newBio;
            document.getElementById('p_Bio').value = newBio; // Update grid
            saveState(); // Commit to memory
            
            spinBioBtn.textContent = 'Spun!';
            setTimeout(() => spinBioBtn.textContent = 'Spin', 1500);
        } else {
            spinBioBtn.textContent = 'Empty!';
            setTimeout(() => spinBioBtn.textContent = 'Spin', 1500);
        }
    });

    // ─── Mappings ───
    const getAvailableTypes = () => {
        return ['FirstName', 'LastName', 'Username', 'Password', 'Email', 'Phone', 'Company', 'JobTitle', 'Website', 'Bio', 'Street', 'City', 'State', 'Zip', 'Country', 'Gender', 'SecurityAnswer', 'DOB_Full', 'DOB_Year', 'DOB_Month', 'DOB_Day'];
    }

    const addMappingRow = (selector = '', type = 'Email') => {
        const row = document.createElement('div');
        row.className = 'mapping-item';
        
        const selInput = document.createElement('input');
        selInput.type = 'text';
        selInput.placeholder = '#id, .class';
        selInput.value = selector;
        selInput.className = 'val-selector';

        const typeSelect = document.createElement('select');
        typeSelect.className = 'val-type';
        getAvailableTypes().forEach(t => {
            const opt = document.createElement('option');
            opt.value = t;
            opt.textContent = t;
            if (t === type) opt.selected = true;
            typeSelect.appendChild(opt);
        });

        const delBtn = document.createElement('button');
        delBtn.textContent = '✕';
        delBtn.className = 'btn-red del-btn';
        delBtn.onclick = () => {
            row.remove();
            saveState();
        };

        row.appendChild(selInput);
        row.appendChild(typeSelect);
        row.appendChild(delBtn);
        mappingsContainer.appendChild(row);
    };

    addMappingBtn.addEventListener('click', () => {
        addMappingRow();
        saveState();
    });

    // ─── Config Builder ───
    const buildConfig = () => {
        const mappings = [];
        document.querySelectorAll('.mapping-item').forEach(row => {
            const selector = row.querySelector('.val-selector').value.trim();
            const type = row.querySelector('.val-type').value;
            if (selector) mappings.push({ selector, type });
        });

        return {
            emailDomain: emailDomainInput.value.trim(),
            passwordLength: parseInt(passwordLengthInput.value) || 12,
            bioSpintax: bioSpintaxInput.value,
            slowType: slowTypeInput.checked,
            typeSpeed: parseInt(typeSpeedInput.value) || 30,
            customMappings: mappings
        };
    };

    const saveState = () => {
        currentConfig = buildConfig();
        chrome.storage.local.set({
            config: currentConfig,
            activePersona: currentPersona
        });
    };

    emailDomainInput.addEventListener('change', saveState);
    passwordLengthInput.addEventListener('change', saveState);
    bioSpintaxInput.addEventListener('change', saveState);
    mappingsContainer.addEventListener('change', saveState);
    mappingsContainer.addEventListener('input', saveState);

    // ─── Saved Profiles System ───
    const refreshProfileDropdown = () => {
        // Clear existing options except the placeholder
        profileSelect.innerHTML = '<option value="">— Select Profile —</option>';
        const names = Object.keys(savedProfiles).sort();
        names.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            profileSelect.appendChild(opt);
        });
    };

    const persistProfiles = () => {
        chrome.storage.local.set({ savedProfiles });
    };

    saveProfileBtn.addEventListener('click', () => {
        const name = profileNameInput.value.trim();
        if (!name) {
            showToast('Enter a profile name!', 'error');
            return;
        }

        // Deep clone persona and config
        savedProfiles[name] = {
            persona: JSON.parse(JSON.stringify(currentPersona)),
            config: JSON.parse(JSON.stringify(currentConfig))
        };
        persistProfiles();
        refreshProfileDropdown();
        profileSelect.value = name;
        profileNameInput.value = '';
        showToast(`Profile "${name}" saved!`, 'success');
    });

    loadProfileBtn.addEventListener('click', () => {
        const name = profileSelect.value;
        if (!name || !savedProfiles[name]) {
            showToast('Select a profile first!', 'error');
            return;
        }

        const profile = savedProfiles[name];
        currentPersona = JSON.parse(JSON.stringify(profile.persona));
        renderPersona();

        // Load config if saved
        if (profile.config) {
            currentConfig = JSON.parse(JSON.stringify(profile.config));
            emailDomainInput.value = currentConfig.emailDomain || '';
            passwordLengthInput.value = currentConfig.passwordLength || 12;
            bioSpintaxInput.value = currentConfig.bioSpintax || '';
            slowTypeInput.checked = currentConfig.slowType || false;
            typeSpeedInput.value = currentConfig.typeSpeed || 30;
            updateSpeedUI();

            // Rebuild mappings
            mappingsContainer.innerHTML = '';
            if (currentConfig.customMappings && currentConfig.customMappings.length > 0) {
                currentConfig.customMappings.forEach(m => addMappingRow(m.selector, m.type));
            } else {
                addMappingRow('', 'Email');
            }
        }

        saveState();
        showToast(`Loaded "${name}"`, 'success');
    });

    deleteProfileBtn.addEventListener('click', () => {
        const name = profileSelect.value;
        if (!name || !savedProfiles[name]) {
            showToast('Select a profile to delete!', 'error');
            return;
        }

        if (confirm(`Delete profile "${name}"?`)) {
            delete savedProfiles[name];
            persistProfiles();
            refreshProfileDropdown();
            showToast(`Deleted "${name}"`, 'success');
        }
    });

    // ─── Import / Export ───
    exportProfilesBtn.addEventListener('click', () => {
        if (Object.keys(savedProfiles).length === 0) {
            showToast('No profiles to export!', 'error');
            return;
        }

        const data = JSON.stringify(savedProfiles, null, 2);
        const blob = new Blob([data], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `autofiller-profiles-${new Date().toISOString().slice(0,10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
        showToast(`Exported ${Object.keys(savedProfiles).length} profiles`, 'success');
    });

    importProfilesBtn.addEventListener('click', () => {
        importFileInput.click();
    });

    importFileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (ev) => {
            try {
                const imported = JSON.parse(ev.target.result);
                if (typeof imported !== 'object' || Array.isArray(imported)) {
                    showToast('Invalid profile format!', 'error');
                    return;
                }

                let count = 0;
                for (const [name, data] of Object.entries(imported)) {
                    if (data.persona) {
                        savedProfiles[name] = data;
                        count++;
                    }
                }

                persistProfiles();
                refreshProfileDropdown();
                showToast(`Imported ${count} profiles!`, 'success');
            } catch (err) {
                showToast('Failed to parse JSON!', 'error');
            }
        };
        reader.readAsText(file);
        importFileInput.value = ''; // Reset
    });

    // ─── Content Script Injection ───
    const injectContentScript = async (tabId) => {
        await chrome.scripting.executeScript({
            target: { tabId },
            files: ['content.js']
        }).catch(e => console.log("[AutoFiller] Injection warn:", e));
    };

    autoFillBtn.addEventListener('click', async () => {
        saveState();
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id) {
            await injectContentScript(tab.id);
            chrome.tabs.sendMessage(tab.id, { 
                action: "AUTO_FILL", 
                persona: currentPersona, 
                config: currentConfig 
            });
        }
    });

    clearFormBtn.addEventListener('click', async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id) {
            await injectContentScript(tab.id);
            chrome.tabs.sendMessage(tab.id, { action: "CLEAR_FORM" });
        }
    });

    extractFormBtn.addEventListener('click', async () => {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id) {
            await injectContentScript(tab.id);
            chrome.tabs.sendMessage(tab.id, { action: "EXTRACT_FORM" }, (response) => {
                if (chrome.runtime.lastError) return;
                if (response && response.persona) {
                    currentPersona = { ...currentPersona, ...response.persona };
                    renderPersona();
                    saveState();
                    extractFormBtn.textContent = 'Extracted!';
                    setTimeout(() => extractFormBtn.textContent = 'Extract Scan', 1500);
                }
            });
        }
    });
});
