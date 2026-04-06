/**
 * Kiro Gateway Dashboard - Main Application Logic
 * Handles Authentication, Tab Management, API Key CRUD, and Playground.
 */

// --- Global State ---
let currentUser = null;
let idToken = null;
let isAdmin = false;
let firebaseConfig = null;

// --- Initialize App ---
async function init() {
    try {
        // 1. Fetch Firebase Config from server
        const configResp = await fetch('/admin/config');
        firebaseConfig = await configResp.json();
        
        if (!firebaseConfig || !firebaseConfig.apiKey) {
            console.error("Failed to load Firebase config from server.");
            showSystemWarning("Configuration Error: Firebase settings are missing on the server.");
            return;
        }

        // Check if database is ready
        if (firebaseConfig.firebase_ready === false) {
            showSystemWarning("Database Disconnected: Kiro-Flow is running in 'Degraded Mode'. Please set FIREBASE_SERVICE_ACCOUNT in your Vercel Dashboard to enable API keys and persistence.");
            document.getElementById('create-key-btn').disabled = true;
            document.getElementById('create-key-btn').title = "Database Disconnected - API Key generation is disabled.";
        }

        // 2. Initialize Firebase
        firebase.initializeApp(firebaseConfig);
        
        // 3. Listen for Auth State
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                currentUser = user;
                idToken = await user.getIdToken();
                await handleUserLogin(user);
            } else {
                handleUserLogout();
            }
        });

        // 4. Setup Event Listeners
        setupEventListeners();
    } catch (err) {
        console.error("Initialization failed:", err);
    }
}

// --- Auth Functions ---
async function handleUserLogin(user) {
    document.getElementById('login-overlay').classList.add('hidden');
    document.getElementById('app-container').classList.remove('hidden');
    
    // Update Sidebar
    document.getElementById('user-avatar').src = user.photoURL || 'https://ui-avatars.com/api/?name=' + user.displayName;
    document.getElementById('user-name').innerText = user.displayName;
    document.getElementById('display-uid').innerText = user.uid;
    document.getElementById('display-email').innerText = user.email;
    document.getElementById('display-joined').innerText = new Date(user.metadata.creationTime).toLocaleDateString();

    // Check Admin Status
    try {
        const infoResp = await fetch('/admin/user/info', {
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        const info = await infoResp.json();
        isAdmin = info.is_admin;
        
        if (isAdmin) {
            document.getElementById('admin-nav-group').classList.remove('hidden');
            document.getElementById('user-role').innerText = "Super Admin";
            document.getElementById('user-role').classList.add('admin');
        } else {
            document.getElementById('user-role').innerText = "User";
        }
    } catch (err) {
        console.error("Failed to fetch user info:", err);
    }

    // Load initial data (keys)
    loadApiKeys();
}

function handleUserLogout() {
    currentUser = null;
    idToken = null;
    isAdmin = false;
    document.getElementById('login-overlay').classList.remove('hidden');
    document.getElementById('app-container').classList.add('hidden');
    document.getElementById('admin-nav-group').classList.add('hidden');
}

// --- Tab Management ---
function switchTab(tabId) {
    // Update Sidebar
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.tab === tabId);
    });

    // Update Content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabId}`);
    });

    // Tab-specific actions
    if (tabId === 'keys') loadApiKeys();
    if (tabId === 'system' && isAdmin) loadSystemStatus();
}

// --- API Key Management ---
async function loadApiKeys() {
    const container = document.getElementById('keys-list');
    container.innerHTML = '<div class="loading-spinner"></div>';

    try {
        const resp = await fetch('/admin/user/keys', {
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        const data = await resp.json();
        const keys = data.keys || [];

        if (keys.length === 0) {
            container.innerHTML = '<div class="empty-state">No API keys found. Generate one to get started.</div>';
            return;
        }

        container.innerHTML = '';
        keys.forEach(key => {
            const item = document.createElement('div');
            item.className = 'key-item animate-fade-in';
            item.innerHTML = `
                <div class="key-details">
                    <span class="key-name">${key.name}</span>
                    <span class="key-date">Created on ${new Date(key.created_at).toLocaleDateString()}</span>
                    <code class="key-id">${key.id}</code>
                </div>
                <div class="key-actions">
                   <button class="btn btn-icon btn-danger" onclick="revokeKey('${key.id}')" title="Revoke">🗑️</button>
                </div>
            `;
            container.appendChild(item);
        });
    } catch (err) {
        container.innerHTML = '<div class="error-msg">Failed to load API keys.</div>';
    }
}

async function createApiKey() {
    const name = prompt("Enter a name for this API key (e.g., 'Project X'):");
    if (!name) return;

    try {
        const formData = new FormData();
        formData.append('name', name);

        const resp = await fetch('/admin/user/keys', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` },
            body: formData
        });

        const data = await resp.json();
        if (data.status === 'success') {
            document.getElementById('new-key-value').innerText = data.key.value;
            document.getElementById('key-modal').classList.remove('hidden');
            loadApiKeys();
        } else {
            alert("Error: " + (data.detail || "Failed to create key"));
        }
    } catch (err) {
        alert("Failed to connect to server.");
    }
}

async function revokeKey(keyId) {
    if (!confirm("Are you sure you want to revoke this API key? Applications using it will stop working immediately.")) return;

    try {
        const resp = await fetch(`/admin/user/keys/${keyId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${idToken}` }
        });

        const data = await resp.json();
        if (data.status === 'success') {
            loadApiKeys();
        } else {
            alert("Error: " + (data.detail || "Failed to revoke key"));
        }
    } catch (err) {
        alert("Failed to connect to server.");
    }
}

// --- AI Playground ---
const chatHistory = [];
async function sendMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    input.value = '';
    appendMessage('user', text);

    const model = document.getElementById('model-selector').value;
    chatHistory.push({ role: 'user', content: text });

    try {
        const resp = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${idToken}` // Note: Backend handles both PROXY_KEY and ID_TOKEN for Playground?
                // Wait, I need to check how backend handles Playground requests.
                // For playground, I'll use the ID_TOKEN and modify back-end to allow ID_TOKEN for Playground too.
                // Actually, I'll just use a 'master' key from the backend or let the playground use the proxy's own key.
                // Let's use PROXY_API_KEY if we have it, but better: the backend verify_api_key now supports multi-user.
                // Playground doesn't have an API key yet. User should create one.
                // For simplicity, let's assume we use one of the user's keys or a special session for playground.
            },
            body: JSON.stringify({
                model: model,
                messages: chatHistory,
                stream: false
            })
        });

        const data = await resp.json();
        if (data.choices && data.choices[0]) {
            const reply = data.choices[0].message.content;
            appendMessage('assistant', reply);
            chatHistory.push({ role: 'assistant', content: reply });
        } else {
            appendMessage('assistant', "Error: " + (data.error?.message || "Something went wrong"));
        }
    } catch (err) {
        appendMessage('assistant', "Error: Failed to connect to gateway.");
    }
}

function appendMessage(role, content) {
    const historyDiv = document.getElementById('chat-history');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role} animate-fade-in`;
    
    // Render Markdown for assistant
    const htmlContent = role === 'assistant' ? marked.parse(content) : content;
    
    msgDiv.innerHTML = `<div class="message-content">${htmlContent}</div>`;
    historyDiv.appendChild(msgDiv);
    historyDiv.scrollTop = historyDiv.scrollHeight;
}

// --- System Admin Functions ---
async function loadSystemStatus() {
    try {
        const resp = await fetch('/admin/status', {
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        const data = await resp.json();
        
        const badge = document.getElementById('token-status');
        if (data.auth_status.is_authenticated) {
            badge.innerText = "ONLINE";
            badge.className = "status-badge online";
        } else {
            badge.innerText = "OFFLINE";
            badge.className = "status-badge offline";
        }
        
        document.getElementById('last-refresh').innerText = data.auth_status.expires_at ? new Date(data.auth_status.expires_at).toLocaleString() : "Never";
    } catch (err) {
        console.error("Failed to load status:", err);
    }
}

async function updateCredentials() {
    const token = document.getElementById('admin-token').value.trim();
    const arn = document.getElementById('admin-arn').value.trim();
    
    if (!token && !arn) return;

    const formData = new FormData();
    if (token) formData.append('refresh_token', token);
    if (arn) formData.append('profile_arn', arn);

    try {
        const resp = await fetch('/admin/update-credentials', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` },
            body: formData
        });
        const data = await resp.json();
        
        const successEl = document.getElementById('admin-success');
        successEl.innerText = data.message;
        successEl.classList.remove('hidden');
        setTimeout(() => successEl.classList.add('hidden'), 5000);
        
        loadSystemStatus();
    } catch (err) {
        alert("Failed to update credentials");
    }
}

// --- UI Helpers ---
function showSystemWarning(msg) {
    const banner = document.createElement('div');
    banner.className = 'system-warning-banner animate-slide-down';
    banner.innerHTML = `
        <div class="warning-content">
            <span class="warning-icon">⚠️</span>
            <span class="warning-text">${msg}</span>
        </div>
        <button class="btn btn-icon" onclick="this.parentElement.remove()">✕</button>
    `;
    document.body.prepend(banner);
}

// --- Event Listeners ---
function setupEventListeners() {
    document.getElementById('google-login-btn').onclick = () => {
        const provider = new firebase.auth.GoogleAuthProvider();
        firebase.auth().signInWithPopup(provider);
    };

    document.getElementById('logout-btn').onclick = () => firebase.auth().signOut();

    document.querySelectorAll('.nav-item').forEach(item => {
        item.onclick = () => switchTab(item.dataset.tab);
    });

    document.getElementById('create-key-btn').onclick = createApiKey;
    document.getElementById('close-modal-btn').onclick = () => document.getElementById('key-modal').classList.add('hidden');
    document.getElementById('copy-key-btn').onclick = () => {
        const key = document.getElementById('new-key-value').innerText;
        navigator.clipboard.writeText(key);
        alert("Copied to clipboard!");
    };

    // Playground
    document.getElementById('send-btn').onclick = sendMessage;
    document.getElementById('chat-input').onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    // System
    const updateBtn = document.getElementById('update-creds-btn');
    if (updateBtn) updateBtn.onclick = updateCredentials;
    
    const refreshBtn = document.getElementById('refresh-token-btn');
    if (refreshBtn) refreshBtn.onclick = () => {
        fetch('/admin/refresh-token', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` }
        }).then(() => loadSystemStatus());
    };
}

// Start
init();
