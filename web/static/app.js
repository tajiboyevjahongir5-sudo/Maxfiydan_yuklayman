let token = localStorage.getItem('admin_token');
let updateInterval;

// DOM Elements
const loginScreen = document.getElementById('login-screen');
const appContainer = document.getElementById('app-container');
const loginBtn = document.getElementById('login-btn');
const passwordInput = document.getElementById('password');
const navLinks = document.querySelectorAll('.nav-links li');
const views = document.querySelectorAll('.view');

// Initialization
if (token) {
    showApp();
    startPolling();
}

// Login Logic
loginBtn.addEventListener('click', async () => {
    const password = passwordInput.value;
    if (!password) return alert('Parolni kiriting');

    try {
        const formData = new URLSearchParams();
        formData.append('username', 'admin');
        formData.append('password', password);

        const response = await fetch('/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            token = data.access_token;
            localStorage.setItem('admin_token', token);
            showApp();
            startPolling();
        } else {
            alert('Parol noto\'g\'ri');
        }
    } catch (e) {
        alert('Server bilan ulanishda xato');
    }
});

// Navigation
navLinks.forEach(link => {
    link.addEventListener('click', () => {
        navLinks.forEach(l => l.classList.remove('active'));
        link.classList.add('active');
        const target = link.getAttribute('data-target');
        views.forEach(v => v.classList.remove('active'));
        document.getElementById(target).classList.add('active');
        fetchData(target);
    });
});

document.getElementById('logout-btn').addEventListener('click', () => {
    localStorage.removeItem('admin_token');
    token = null;
    loginScreen.style.display = 'flex';
    appContainer.style.display = 'none';
    clearInterval(updateInterval);
});

// API Helper
async function apiCall(endpoint, method = 'GET') {
    const response = await fetch(`/api/${endpoint}`, {
        method,
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    
    if (response.status === 401) {
        document.getElementById('logout-btn').click();
        throw new Error('Unauthorized');
    }
    
    return response.json();
}

// Data Fetching
function showApp() {
    loginScreen.style.display = 'none';
    appContainer.style.display = 'grid';
    fetchData('dashboard');
}

function startPolling() {
    updateInterval = setInterval(() => {
        const activeView = document.querySelector('.view.active').id;
        fetchData(activeView);
    }, 5000);
}

async function fetchData(view) {
    try {
        if (view === 'dashboard') {
            const stats = await apiCall('stats');
            document.getElementById('total-req').textContent = stats.total_requests;
            document.getElementById('success-req').textContent = stats.success;
            document.getElementById('error-req').textContent = stats.errors;
            document.getElementById('active-users').textContent = stats.active_users;
        } 
        else if (view === 'logs') {
            const logs = await apiCall('logs');
            const tbody = document.getElementById('logs-table');
            tbody.innerHTML = '';
            logs.forEach(log => {
                const tr = document.createElement('tr');
                const date = new Date(log.time * 1000).toLocaleString();
                const badge = log.status === 'success' ? 'success' : 'error';
                tr.innerHTML = `
                    <td>${date}</td>
                    <td>${log.user_name} (${log.user_id})</td>
                    <td><a href="${log.link}" target="_blank" style="color:var(--tg-theme-link-color)">Havola</a></td>
                    <td><span class="badge ${badge}">${log.status}</span></td>
                    <td>${log.error_msg || '-'}</td>
                `;
                tbody.appendChild(tr);
            });
        }
        else if (view === 'users') {
            const users = await apiCall('users');
            const tbody = document.getElementById('users-table');
            tbody.innerHTML = '';
            Object.keys(users).forEach(uid => {
                const u = users[uid];
                const tr = document.createElement('tr');
                const lastSeen = new Date(u.last_seen * 1000).toLocaleString();
                const btnClass = u.banned ? 'btn-success' : 'btn-danger';
                const btnText = u.banned ? 'Ruxsat berish' : 'Taqiqlash';
                tr.innerHTML = `
                    <td>${uid}</td>
                    <td>${u.name}</td>
                    <td>${u.requests}</td>
                    <td>${lastSeen}</td>
                    <td><button class="action-btn ${btnClass}" onclick="toggleBan(${uid})">${btnText}</button></td>
                `;
                tbody.appendChild(tr);
            });
        }
        else if (view === 'settings') {
            const config = await apiCall('config');
            document.getElementById('set-max-size').textContent = config.MAX_FILE_SIZE_MB;
            document.getElementById('set-dl-dir').textContent = config.DOWNLOAD_DIR;
            document.getElementById('set-allowed').textContent = config.ALLOWED_USERS || 'Hamma ruxsatli';
        }
    } catch (e) {
        console.error(e);
    }
}

async function toggleBan(userId) {
    await apiCall(`users/${userId}/toggle_ban`, 'POST');
    fetchData('users');
}
