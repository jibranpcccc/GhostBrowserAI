// This script is injected into every page by the extension
const testDiv = document.createElement('div');
testDiv.id = 'ai-extension-test-div';
testDiv.style.display = 'none';
testDiv.innerText = 'EXTENSION_SUCCESSFULLY_LOADED';
document.body.appendChild(testDiv);
console.log("AI Browser Dummy Extension loaded successfully!");
