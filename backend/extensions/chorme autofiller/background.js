chrome.commands.onCommand.addListener(async (command) => {
    if (command === "quick-autofill" || command === "quick-extract") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.id) return;

        // Ensure the heuristic framework is present
        await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ['content.js']
        }).catch(e => console.log("[AutoFiller] Injection warn:", e));

        // Pull active state and bridge it
        chrome.storage.local.get(['activePersona', 'config'], (result) => {
            const currentPersona = result.activePersona || {};
            const currentConfig = result.config || {};

            if (command === "quick-autofill") {
                chrome.tabs.sendMessage(tab.id, { 
                    action: "AUTO_FILL", 
                    persona: currentPersona, 
                    config: currentConfig 
                });
            } else if (command === "quick-extract") {
                chrome.tabs.sendMessage(tab.id, { action: "EXTRACT_FORM" }, (response) => {
                    if (chrome.runtime.lastError) return;
                    if (response && response.persona) {
                        const newPersona = { ...currentPersona, ...response.persona };
                        chrome.storage.local.set({ activePersona: newPersona });
                    }
                });
            }
        });
    }
});
