import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import { 
    getAuth, 
    onAuthStateChanged, 
    signInWithPopup, 
    GoogleAuthProvider, 
    signOut 
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";

// State
let idToken = null;
const API_PREFIX = '/admin';

// DOM Elements
const loginView = document.getElementById('login-view');
const dashboardView = document.getElementById('dashboard-view');
const googleLoginBtn = document.getElementById('google-login-btn');
const logoutBtn = document.getElementById('logout-btn');
const loginError = document.getElementById('login-error');
const updateModal = document.getElementById('update-modal');
const updateForm = document.getElementById('update-form');
const showUpdateBtn = document.getElementById('show-update-btn');
const refreshNowBtn = document.getElementById('refresh-now-btn');
const closeBtn = document.querySelector('.close-btn');
const toast = document.getElementById('toast');

// UI Utilities
function showToast(message, duration = 3000) {
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), duration);
}

function toggleView(viewName) {
    if (viewName === 'dashboard') {
        loginView.style.display = 'none';
        dashboardView.style.display = 'block';
    } else {
        loginView.style.display = 'block';
        dashboardView.style.display = 'none';
    }
}

async function updateStatus() {
    if (!idToken) return;

    try {
        const response = await fetch(`${API_PREFIX}/status`, {
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        
        if (response.status === 403) {
            const data = await response.json();
            showToast(data.detail);
            await signOut(auth);
            return;
        }

        if (!response.ok) throw new Error('Authentication failed');
        
        const data = await response.json();
        
        // Update UI
        document.getElementById('app-version').textContent = `v${data.app_version}`;
        document.getElementById('user-email').textContent = data.admin_email;
        
        const statusIndicator = document.getElementById('auth-is-authenticated');
        if (data.auth_status.is_authenticated) {
            statusIndicator.textContent = '● Online / Authenticated';
            statusIndicator.className = 'status-indicator success';
        } else {
            statusIndicator.textContent = '● Offline / Not Authenticated';
            statusIndicator.className = 'status-indicator error';
        }

        document.getElementById('auth-type').textContent = `Type: ${data.auth_status.auth_type}`;
        document.getElementById('auth-region').textContent = data.auth_status.region || 'Unknown';
        
        const exp = data.auth_status.expires_at;
        if (exp) {
            document.getElementById('auth-expires-at').textContent = new Date(exp).toLocaleString();
        } else {
            document.getElementById('auth-expires-at').textContent = 'N/A';
        }

    } catch (err) {
        console.error(err);
        showToast('Failed to fetch status');
    }
}

// Initialize App
let auth;

async function initRemoteAdmin() {
    try {
        const response = await fetch(`${API_PREFIX}/config`);
        const config = await response.json();
        
        const app = initializeApp(config);
        auth = getAuth(app);

        const provider = new GoogleAuthProvider();

        onAuthStateChanged(auth, async (user) => {
            if (user) {
                try {
                    idToken = await user.getIdToken(true);
                    toggleView('dashboard');
                    updateStatus();
                } catch (err) {
                    console.error("Token error", err);
                    showToast("Failed to refresh session");
                }
            } else {
                idToken = null;
                toggleView('login');
            }
        });

        googleLoginBtn.onclick = async () => {
            try {
                loginError.style.display = 'none';
                await signInWithPopup(auth, provider);
            } catch (err) {
                console.error(err);
                loginError.textContent = err.message.split(' (')[0];
                loginError.style.display = 'block';
            }
        };

        logoutBtn.onclick = () => signOut(auth);

    } catch (err) {
        console.error("Config fetch failed", err);
        showToast("Backend connection error");
    }
}

// Action Handlers
refreshNowBtn.addEventListener('click', async () => {
    refreshNowBtn.disabled = true;
    refreshNowBtn.textContent = 'Refreshing...';

    try {
        const response = await fetch(`${API_PREFIX}/refresh-token`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        const data = await response.json();
        
        if (response.ok) {
            showToast('Token refreshed successfully');
            updateStatus();
        } else {
            showToast(`Error: ${data.detail}`);
        }
    } catch (err) {
        showToast('Connection error');
    } finally {
        refreshNowBtn.disabled = false;
        refreshNowBtn.textContent = 'Manual Refresh';
    }
});

updateForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData();
    
    const tokenVal = document.getElementById('update-refresh-token').value;
    const clientId = document.getElementById('update-client-id').value;
    const clientSecret = document.getElementById('update-client-secret').value;
    const region = document.getElementById('update-region').value;

    if (tokenVal) formData.append('refresh_token', tokenVal);
    if (clientId) formData.append('client_id', clientId);
    if (clientSecret) formData.append('client_secret', clientSecret);
    if (region) formData.append('region', region);

    try {
        const response = await fetch(`${API_PREFIX}/update-credentials`, {
            method: 'POST',
            body: formData,
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        const data = await response.json();
        
        showToast(data.message);
        if (response.ok) {
            updateModal.style.display = 'none';
            updateStatus();
        }
    } catch (err) {
        showToast('Failed to update credentials');
    }
});

// Modal UI
showUpdateBtn.onclick = () => updateModal.style.display = 'flex';
closeBtn.onclick = () => updateModal.style.display = 'none';
window.onclick = (e) => { if (e.target === updateModal) updateModal.style.display = 'none'; };

// Start the app
initRemoteAdmin();
