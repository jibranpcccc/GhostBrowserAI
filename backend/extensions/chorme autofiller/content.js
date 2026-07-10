if (!window._autoFillerInjected) {
    window._autoFillerInjected = true;

    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
        if (message.action === "AUTO_FILL") {
            autoFillPage(message);
        } else if (message.action === "CLEAR_FORM") {
            clearForms();
        } else if (message.action === "EXTRACT_FORM") {
            const extractedPersona = extractForm();
            sendResponse({ persona: extractedPersona });
        }
        return true; 
    });

    const dispatchEvents = (el) => {
        // Safe dispatching. Input triggers React updates. Change triggers Vue/Legacy.
        el.dispatchEvent(new Event('input', { bubbles: true }));
        // Only trigger 'change' on selects, or inputs where React explicitly relies on it. 
        // We avoid firing generic change on everything if possible to prevent legacy refreshes, 
        // but practically many frameworks require it.
        el.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const fillElement = async (el, value, slowType = false, speed = 30) => {
        if (!el || el.disabled || el.readOnly || el.type === 'hidden') return;
        
        // Handle Checkboxes natively
        if (el.type === 'checkbox') {
            if (!el.checked && String(value).toLowerCase() === 'true') {
                el.checked = true;
                dispatchEvents(el);
            }
            if (slowType) await new Promise(r => setTimeout(r, Math.random() * speed + speed));
            return;
        }

        // Handle Radios 
        if (el.type === 'radio') {
            if (el.value && String(el.value).toLowerCase() === String(value).toLowerCase()) {
                el.checked = true;
                dispatchEvents(el);
            }
            if (slowType) await new Promise(r => setTimeout(r, Math.random() * speed + speed));
            return;
        }

        // Handle Selects
        if(el.tagName === 'SELECT') {
             let matched = false;
             for(let i=0; i<el.options.length; i++){
                 if(el.options[i].text.toLowerCase() === String(value).toLowerCase() || el.options[i].value.toLowerCase() === String(value).toLowerCase()){
                     el.selectedIndex = i;
                     matched = true;
                     break;
                 }
             }
             if(!matched && el.options.length > 1) {
                 for(let i=0; i<el.options.length; i++){
                     if(el.options[i].text.toLowerCase().includes(String(value).toLowerCase())) {
                         el.selectedIndex = i; break;
                     }
                 }
             }
             // For selects, 'change' is absolutely vital, but can cause page load.
             dispatchEvents(el);
             if (slowType) await new Promise(r => setTimeout(r, Math.random() * speed * 2 + speed * 2));
             return;
        } 

        // Handling standard inputs & textareas natively for React
        // This avoids just doing el.value = value causing React to miss the update entirely
        try {
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
            const nativeTextareaValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
            
            const setter = el.tagName === 'TEXTAREA' ? nativeTextareaValueSetter : nativeInputValueSetter;

            if (slowType) {
                if (setter) setter.call(el, ""); else el.value = "";
                el.dispatchEvent(new Event('input', { bubbles: true }));
                
                let strVal = String(value);
                for (let i = 0; i < strVal.length; i++) {
                    const currentVal = strVal.substring(0, i + 1);
                    if (setter) setter.call(el, currentVal); else el.value = currentVal;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    await new Promise(r => setTimeout(r, Math.random() * speed + speed * 0.5)); // Delay between chars
                }
                await new Promise(r => setTimeout(r, Math.random() * speed * 2 + speed)); // Delay after field
            } else {            
                if (setter) setter.call(el, value);
                else el.value = value;
                // Dispatch input after native setter
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
        } catch (e) {
            // Fallback for ultra-legacy
            if (slowType) {
                el.value = "";
                dispatchEvents(el);
                let strVal = String(value);
                for (let i = 0; i < strVal.length; i++) {
                    el.value = strVal.substring(0, i + 1);
                    dispatchEvents(el);
                    await new Promise(r => setTimeout(r, Math.random() * speed + speed * 0.5));
                }
                await new Promise(r => setTimeout(r, Math.random() * speed * 2 + speed));
            } else {                
                el.value = value;
                dispatchEvents(el);
            }
        }
    };

    function clearForms() {
        const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="image"]):not([type="file"])');
        const textareas = document.querySelectorAll('textarea');
        const selects = document.querySelectorAll('select');
        
        [...inputs, ...textareas].forEach(input => {
            if (input.type === 'checkbox' || input.type === 'radio') {
                if (input.checked) { input.checked = false; dispatchEvents(input); }
            } else {
                fillElement(input, "");
            }
        });
        selects.forEach(s => { s.selectedIndex = 0; s.dispatchEvent(new Event('change', {bubbles: true})); });
        console.log("[AutoFiller Pro] Cleared forms.");
    }

    function extractForm() {
        const elements = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="image"]):not([type="file"]), textarea, select');
        let extracted = {};

        elements.forEach(input => {
            let val = input.value;
            if (input.type === 'checkbox' || input.type === 'radio') {
                if (!input.checked) return;
                val = input.value || "true";
            } else if (input.tagName === 'SELECT') {
                if (input.selectedIndex >= 0) val = input.options[input.selectedIndex].text;
            }
            
            if (!val || String(val).trim() === "") return;

            const attributesStr = [
                input.id || '',
                input.name || '',
                input.className || '',
                input.placeholder || '',
                input.title || ''
            ].join(' ').toLowerCase();

            if (/(first.*name|fname)/i.test(attributesStr)) { extracted.FirstName = val; }
            else if (/(last.*name|lname)/i.test(attributesStr)) { extracted.LastName = val; }
            else if (/(user.*name|uname|login)/i.test(attributesStr)) { extracted.Username = val; }
            else if (/(mail)/i.test(attributesStr)) { extracted.Email = val; }
            else if (/(pass|pwd)/i.test(attributesStr)) { extracted.Password = val; }
            else if (/(phone|mobile|cell|tel)/i.test(attributesStr)) { extracted.Phone = val; }
            else if (/(website|url|blog|domain|site)/i.test(attributesStr)) { extracted.Website = val; }
            else if (/(job|title|position|profession|role)/i.test(attributesStr)) { extracted.JobTitle = val; }
            else if (/(bio|about|desc|portrait|summary)/i.test(attributesStr)) { extracted.Bio = val; }
            else if (/(company|organization|biz)/i.test(attributesStr)) { extracted.Company = val; }
            else if (/(street|address1|add1|line1)/i.test(attributesStr)) { extracted.Street = val; }
            else if (/(city|town)/i.test(attributesStr)) { extracted.City = val; }
            else if (/(state|province|region)/i.test(attributesStr)) { extracted.State = val; }
            else if (/(zip|postal)/i.test(attributesStr)) { extracted.Zip = val; }
            else if (/(dob|birth.*date)/i.test(attributesStr)) { extracted.DOB_Full = val; }
            else if (/(birth.*year|year.*birth|yyyy)/i.test(attributesStr)) { extracted.DOB_Year = val; }
            else if (/(birth.*month|month.*birth|mm)/i.test(attributesStr)) { extracted.DOB_Month = val; }
            else if (/(birth.*day|day.*birth|dd)/i.test(attributesStr)) { extracted.DOB_Day = val; }
            else if (/(gender|sex)/i.test(attributesStr)) { extracted.Gender = val; }
            else if (/(country|nation)/i.test(attributesStr)) { extracted.Country = val; }
            else if (/(answer|secret|question)/i.test(attributesStr)) { extracted.SecurityAnswer = val; }
        });

        console.log("[AutoFiller Pro] Form Extracted:", extracted);
        return extracted;
    }

    async function autoFillPage({ persona, config }) {
        if (!persona) return;
        const slow = config && config.slowType;
        const speed = (config && config.typeSpeed) || 30;

        // Expanded query selector to capture Checkboxes and Radios now
        const elements = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="image"]):not([type="file"]), textarea, select');

        if (config && config.customMappings && config.customMappings.length > 0) {
            for (const mapping of config.customMappings) {
                if (!mapping.selector || !mapping.type || !persona[mapping.type]) continue;
                try {
                    const els = document.querySelectorAll(mapping.selector);
                    for (const el of els) {
                        await fillElement(el, persona[mapping.type], slow, speed);
                    }
                } catch(e) {}
            }
        }

        for (const input of elements) {
            // Only skip already filled standard inputs. Allow filling unchecked radios/checks.
            if (input.type !== 'checkbox' && input.type !== 'radio' && input.type !== 'select-one') {
                if (input.value && input.value !== "") continue;
            }

            const attributesStr = [
                input.id || '',
                input.name || '',
                input.className || '',
                input.placeholder || '',
                input.title || ''
            ].join(' ').toLowerCase();

            // Explicit Checkbox targeting for Terms of Service / Agreement boxes
            if (input.type === 'checkbox') {
                const parentText = (input.parentElement ? input.parentElement.textContent.toLowerCase() : '');
                const labelText = attributesStr + ' ' + parentText;
                if (/(term|agree|tos|policy|condition|accept|read|understand|robot|privacy)/i.test(labelText)) {
                    await fillElement(input, 'true', slow, speed);
                }
                continue; // Stop processing further mappings for checkboxes
            }

            // Radio Gender matching
            if (input.type === 'radio') {
                if (/(gender|sex)/i.test(attributesStr)) {
                    await fillElement(input, persona.Gender, slow, speed);
                } else {
                    // Try by value if field attributes are ambiguous
                    if (String(input.value).toLowerCase() === String(persona.Gender).toLowerCase()) {
                        await fillElement(input, persona.Gender, slow, speed);
                    }
                }
                continue;
            }

            // Standard Heuristics
            if (/(first.*name|fname)/i.test(attributesStr)) { await fillElement(input, persona.FirstName, slow, speed); }
            else if (/(last.*name|lname)/i.test(attributesStr)) { await fillElement(input, persona.LastName, slow, speed); }
            else if (/(user.*name|uname|login)/i.test(attributesStr)) { await fillElement(input, persona.Username, slow, speed); }
            else if (/(mail)/i.test(attributesStr)) { await fillElement(input, persona.Email, slow, speed); }
            else if (/(pass|pwd)/i.test(attributesStr)) { await fillElement(input, persona.Password, slow, speed); }
            else if (/(phone|mobile|cell|tel)/i.test(attributesStr)) { await fillElement(input, persona.Phone, slow, speed); }
            else if (/(website|url|blog|domain|site)/i.test(attributesStr)) { await fillElement(input, persona.Website, slow, speed); }
            else if (/(job|title|position|profession|role)/i.test(attributesStr)) { await fillElement(input, persona.JobTitle, slow, speed); }
            else if (/(bio|about|desc|portrait|summary)/i.test(attributesStr)) { await fillElement(input, persona.Bio, slow, speed); }
            else if (/(company|organization|biz)/i.test(attributesStr)) { await fillElement(input, persona.Company, slow, speed); }
            else if (/(street|address1|add1|line1)/i.test(attributesStr)) { await fillElement(input, persona.Street, slow, speed); }
            else if (/(city|town)/i.test(attributesStr)) { await fillElement(input, persona.City, slow, speed); }
            else if (/(state|province|region)/i.test(attributesStr)) { await fillElement(input, persona.State, slow, speed); }
            else if (/(zip|postal)/i.test(attributesStr)) { await fillElement(input, persona.Zip, slow, speed); }
            else if (/(dob|birth.*date)/i.test(attributesStr)) { await fillElement(input, persona.DOB_Full, slow, speed); }
            else if (/(birth.*year|year.*birth|yyyy)/i.test(attributesStr)) { await fillElement(input, persona.DOB_Year, slow, speed); }
            else if (/(birth.*month|month.*birth|mm)/i.test(attributesStr)) { await fillElement(input, persona.DOB_Month, slow, speed); }
            else if (/(birth.*day|day.*birth|dd)/i.test(attributesStr)) { await fillElement(input, persona.DOB_Day, slow, speed); }
            else if (/(country|nation)/i.test(attributesStr)) { await fillElement(input, persona.Country, slow, speed); }
            else if (/(answer|secret|question)/i.test(attributesStr)) { await fillElement(input, persona.SecurityAnswer, slow, speed); }
        }

        console.log("[AutoFiller Pro] Auto-fill complete. Used Persona:", persona);
    }
}
