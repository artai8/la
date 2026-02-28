let ws = null;
let currentPhone = '';
let apiEditId = null;
let proxyEditId = null;

// ==================== WebSocket ====================
function connectWS() {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${protocol}://${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('connStatus').innerHTML = '<span class="dot connected"></span> 已连接';
    };

    ws.onclose = () => {
        document.getElementById('connStatus').innerHTML = '<span class="dot disconnected"></span> 未连接';
        setTimeout(connectWS, 3000);
    };

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'state') updateState(msg.data);
        if (msg.type === 'log') appendLog(msg.channel, msg.text);
    };
}

function updateState(d) {
    document.getElementById('extractStatus').textContent = d.extract ? '🟢 运行中' : '⚪ 未运行';
    document.getElementById('extractCount').textContent = d.members_ext_count;
    document.getElementById('loadedCount').textContent = d.members_count;
    document.getElementById('okCount').textContent = d.ok_count;
    document.getElementById('badCount').textContent = d.bad_count;
    document.getElementById('runCount').textContent = d.runs.length;
    document.getElementById('finalCount').textContent = d.final.length;
    document.getElementById('adderStatus').textContent = d.status ? '🟢 运行中' : '⚪ 未运行';
    const ct = document.getElementById('currentTaskInfo');
    if (ct) ct.textContent = d.current_task_id ? `#${d.current_task_id} ${d.current_task_type}` : '空闲';
}

function appendLog(ch, text) {
    const box = document.getElementById(ch === 'extract' ? 'extractLog' : 'adderLog');
    box.textContent += text + '\n';
    box.scrollTop = box.scrollHeight;
}

// ==================== Tabs ====================
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'account') refreshAccounts();
        if (btn.dataset.tab === 'extract') refreshGroups();
        if (btn.dataset.tab === 'adder') { refreshLoadGroups(); refreshAccounts(); }
        if (btn.dataset.tab === 'tasks') { refreshTasks(); refreshTaskGroups(); }
        if (btn.dataset.tab === 'admin') { loadSettings(); refreshApis(); refreshProxies(); refreshLists(); refreshUsers(); refreshWorkers(); }
    });
});

// ==================== Accounts Actions ====================
let selectedAccounts = [];

async function refreshAccounts() {
    const d = await api('/api/accounts');
    const list = document.getElementById('accountList');
    document.getElementById('accountCount').textContent = d.count || 0;
    list.innerHTML = '';
    
    // Group accounts by group_name
    const grouped = {};
    (d.accounts || []).forEach(a => {
        const g = a.group || 'default';
        if (!grouped[g]) grouped[g] = [];
        grouped[g].push(a);
    });

    for (const [gname, accs] of Object.entries(grouped)) {
        list.innerHTML += `<div class="group-header" style="background:#f0f0f0;padding:5px;margin-top:5px;font-weight:bold">${gname} (${accs.length}) <button class="btn btn-sm" onclick="selectGroup('${gname}')">全选</button></div>`;
        accs.forEach(a => {
            const isSel = selectedAccounts.includes(a.phone) ? 'checked' : '';
            list.innerHTML += `<div class="list-item"><input type="checkbox" onchange="toggleAccountSelection('${a.phone}')" ${isSel}> <i class="fas fa-user-check"></i> ${a.phone}</div>`;
        });
    }
}

function toggleAccountSelection(phone) {
    if (selectedAccounts.includes(phone)) {
        selectedAccounts = selectedAccounts.filter(p => p !== phone);
    } else {
        selectedAccounts.push(phone);
    }
}

function selectGroup(gname) {
    // Need to re-fetch or store locally to know which phones are in group. 
    // For simplicity, just check checkboxes in DOM for now or re-fetch.
    // Let's re-fetch to be safe or parse DOM.
    // Simple DOM parsing:
    // This is a bit hacky but works for now.
    // Better: refreshAccounts stores data in a variable.
    // Implementation:
    api('/api/accounts').then(d => {
        const accs = d.accounts.filter(a => (a.group || 'default') === gname);
        accs.forEach(a => {
            if (!selectedAccounts.includes(a.phone)) selectedAccounts.push(a.phone);
        });
        refreshAccounts();
    });
}

async function batchSetGroup() {
    const group_name = document.getElementById('batchGroupName').value.trim();
    if (!group_name) return showToast('请输入分组名', 'warning');
    if (!selectedAccounts.length) return showToast('未选择账号', 'warning');
    
    const d = await api('/api/accounts/group/set', 'POST', { phones: selectedAccounts, group_name });
    showToast(d.status ? '分组已更新' : d.message, d.status ? 'success' : 'error');
    if (d.status) {
        selectedAccounts = [];
        refreshAccounts();
    }
}

function showProfileEdit() {
    if (!selectedAccounts.length) return showToast('未选择账号', 'warning');
    document.getElementById('profileEditForm').style.display = 'block';
}

function hideProfileEdit() {
    document.getElementById('profileEditForm').style.display = 'none';
}

async function batchUpdateProfile() {
    const first_name = document.getElementById('editFirstName').value.trim();
    const last_name = document.getElementById('editLastName').value.trim();
    const about = document.getElementById('editAbout').value.trim();
    const username = document.getElementById('editUsername').value.trim();
    
    if (!first_name && !last_name && !about && !username) return showToast('请至少填写一项', 'warning');
    
    const d = await api('/api/accounts/profile/update', 'POST', {
        phones: selectedAccounts,
        first_name: first_name || null,
        last_name: last_name || null,
        about: about || null,
        username: username || null
    });
    
    if (d.status) {
        let success = 0;
        let fail = 0;
        d.results.forEach(r => { if (r.ok) success++; else fail++; });
        showToast(`更新成功 ${success}，失败 ${fail}`, 'info');
        hideProfileEdit();
    } else {
        showToast(d.message, 'error');
    }
}

// ==================== Toast ====================
function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type} show`;
    setTimeout(() => t.classList.remove('show'), 4000);
}

// ==================== API ====================
async function api(url, method = 'GET', body = null) {
    try {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        const r = await fetch(url, opts);
        if (r.status === 401) {
            location.href = '/login';
            return { status: false, message: '未登录' };
        }
        return await r.json();
    } catch (e) {
        showToast('网络错误', 'error');
        return { status: false, message: '网络错误' };
    }
}

// ==================== Accounts ====================
async function checkAccountHealth() {
    const list = document.getElementById('accountHealthList');
    list.innerHTML = '检测中...';
    const d = await api('/api/accounts/health');
    list.innerHTML = '';
    (d.items || []).forEach(it => {
        const icon = it.ok ? 'fa-check-circle' : 'fa-times-circle';
        const color = it.ok ? 'ok' : 'fail';
        list.innerHTML += `<div class="list-item ${color}"><i class="fas ${icon}"></i> ${it.phone} ${it.message || ''}</div>`;
    });
}

async function sendCode() {
    let phone = document.getElementById('phoneInput').value.trim();
    if (!phone) return showToast('请输入手机号', 'warning');
    phone = phone.replace(/[^\d+]/g, '');
    if (phone.startsWith('00')) phone = '+' + phone.slice(2);
    if (!phone.startsWith('+') && /^\d+$/.test(phone)) phone = '+' + phone;
    document.getElementById('phoneInput').value = phone;

    const btn = document.getElementById('sendCodeBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 发送中...';

    const d = await api('/api/account/send-code', 'POST', { phone });
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane"></i> 发送验证码';

    if (d.status) {
        currentPhone = d.phone || phone;
        showToast('验证码已发送', 'success');
        setLoginStatus('请查看 Telegram 验证码', 'info');
        document.getElementById('codeSection').style.display = 'block';
        document.getElementById('codeInput').focus();
    } else {
        showToast(d.message, 'error');
        setLoginStatus(d.message, 'error');
    }
}

async function verifyCode() {
    const code = document.getElementById('codeInput').value.trim();
    if (code.length < 4 || code.length > 6 || !/^\d+$/.test(code)) return showToast('请输入4-6位验证码', 'warning');

    const d = await api('/api/account/verify-code', 'POST', { phone: currentPhone, code });
    if (d.status) {
        showToast(d.message, 'success');
        setLoginStatus(d.message, 'success');
        resetLoginUI();
        refreshAccounts();
    } else if (d.needs === 'password') {
        showToast('需要二步验证', 'warning');
        setLoginStatus('请输入二步验证密码', 'info');
        document.getElementById('codeSection').style.display = 'none';
        document.getElementById('passwordSection').style.display = 'block';
        document.getElementById('passwordInput').focus();
    } else {
        showToast(d.message, 'error');
        setLoginStatus(d.message, 'error');
    }
}

async function verifyPassword() {
    const pw = document.getElementById('passwordInput').value;
    if (!pw) return showToast('请输入密码', 'warning');

    const d = await api('/api/account/verify-password', 'POST', { phone: currentPhone, password: pw });
    if (d.status) {
        showToast(d.message, 'success');
        resetLoginUI();
        refreshAccounts();
    } else {
        showToast(d.message, 'error');
        setLoginStatus(d.message, 'error');
    }
}

async function cancelLogin() {
    if (currentPhone) await api('/api/account/cancel', 'POST', { phone: currentPhone });
    resetLoginUI();
    showToast('已取消', 'info');
}

function resetLoginUI() {
    document.getElementById('codeSection').style.display = 'none';
    document.getElementById('passwordSection').style.display = 'none';
    document.getElementById('codeInput').value = '';
    document.getElementById('passwordInput').value = '';
    currentPhone = '';
}

function setLoginStatus(msg, type) {
    const el = document.getElementById('loginStatus');
    el.textContent = msg;
    el.className = `status-msg ${type}`;
}

async function removeAccount() {
    const phone = document.getElementById('removePhoneInput').value.trim();
    if (!phone) return showToast('请输入手机号', 'warning');
    const d = await api('/api/account/remove', 'POST', { phone });
    showToast(d.message, d.status ? 'success' : 'error');
    if (d.status) { document.getElementById('removePhoneInput').value = ''; refreshAccounts(); }
}

async function importSession() {
    const api_id = parseInt(document.getElementById('sessionApiId').value);
    const api_hash = document.getElementById('sessionApiHash').value.trim();
    const session_string = document.getElementById('sessionString').value.trim();
    if (!api_id || !api_hash || !session_string) return showToast('请填写完整信息', 'warning');
    const d = await api('/api/account/import/session', 'POST', { api_id, api_hash, session_string });
    showToast(d.status ? `已导入 ${d.phone}` : d.message, d.status ? 'success' : 'error');
    if (d.status) {
        document.getElementById('sessionString').value = '';
        refreshAccounts();
    }
}

async function importSessionBatch() {
    const lines = document.getElementById('sessionBatchLines').value.trim();
    if (!lines) return showToast('请输入内容', 'warning');
    const d = await api('/api/account/import/session/batch', 'POST', { lines });
    if (!d.status) return showToast(d.message, 'error');
    const box = document.getElementById('sessionBatchResult');
    box.innerHTML = '';
    let ok = 0;
    (d.results || []).forEach(r => {
        const status = r.ok ? 'ok' : 'fail';
        if (r.ok) ok++;
        box.innerHTML += `<div class="list-item ${status}">${r.ok ? '✅' : '❌'} ${r.phone || ''} ${r.message || r.line}</div>`;
    });
    showToast(`完成，成功 ${ok} 条`, 'info');
    refreshAccounts();
}

async function startKeepalive() {
    if (!selectedAccounts.length) return showToast('未选择账号', 'warning');
    const d = await api('/api/accounts/keepalive/start', 'POST', { phones: selectedAccounts });
    showToast(d.status ? `已在线 ${d.count || 0}` : d.message, d.status ? 'success' : 'error');
}

async function stopKeepalive() {
    const d = await api('/api/accounts/keepalive/stop', 'POST');
    showToast(d.status ? '已下线' : d.message, d.status ? 'success' : 'error');
}

async function startWarmup() {
    if (!selectedAccounts.length) return showToast('未选择账号', 'warning');
    const duration_min = parseInt(document.getElementById('warmupDuration').value);
    const actions = (document.getElementById('warmupActions').value || '').split(',').map(v => v.trim()).filter(Boolean);
    const d = await api('/api/accounts/warmup/start', 'POST', { phones: selectedAccounts, duration_min, actions });
    showToast(d.status ? `养号已启动 ${d.count || 0}` : d.message, d.status ? 'success' : 'error');
}

async function checkSpamRestriction() {
    if (!selectedAccounts.length) return showToast('未选择账号', 'warning');
    const list = document.getElementById('accountRestrictionList');
    list.innerHTML = '检测中...';
    const d = await api('/api/accounts/spam/check', 'POST', { phones: selectedAccounts });
    list.innerHTML = '';
    if (!d.status) return showToast(d.message, 'error');
    (d.items || []).forEach(it => {
        const ok = it.ok;
        const limited = it.limited;
        const text = it.message || '';
        const cls = ok ? (limited ? 'fail' : 'ok') : 'fail';
        const label = ok ? (limited ? '受限' : '正常') : '失败';
        list.innerHTML += `<div class="list-item ${cls}"><i class="fas ${ok ? (limited ? 'fa-ban' : 'fa-check-circle') : 'fa-times-circle'}"></i> ${it.phone} ${label} ${text}</div>`;
    });
}

// ==================== Extract ====================
async function refreshGroups() {
    const d = await api('/api/groups');
    ['groupSelect', 'loadGroupSelect'].forEach(id => {
        const sel = document.getElementById(id);
        if (sel) {
            sel.innerHTML = '';
            (d.groups || []).forEach(g => { sel.innerHTML += `<option value="${g}">${g}</option>`; });
        }
    });
}

async function refreshTaskGroups() {
    const d = await api('/api/groups');
    const sel = document.getElementById('taskGroupSelect');
    if (sel) {
        sel.innerHTML = '';
        (d.groups || []).forEach(g => { sel.innerHTML += `<option value="${g}">${g}</option>`; });
    }
}

function toggleTaskType() {
    const type = document.getElementById('taskType').value;
    const extract = document.getElementById('taskExtractSection');
    const batch = document.getElementById('taskBatchExtractSection');
    const adder = document.getElementById('taskAdderSection');
    const invite = document.getElementById('taskInviteSection');
    const join = document.getElementById('taskJoinSection');
    const chat = document.getElementById('taskChatSection');
    const dm = document.getElementById('taskDMSection');
    const warmup = document.getElementById('taskWarmupSection');
    const sequence = document.getElementById('taskSequenceSection');
    const scrape = document.getElementById('taskScrapeSection');
    extract.style.display = type === 'extract' ? 'block' : 'none';
    batch.style.display = type === 'extract_batch' ? 'block' : 'none';
    adder.style.display = type === 'adder' ? 'block' : 'none';
    invite.style.display = type === 'invite' ? 'block' : 'none';
    join.style.display = type === 'join' ? 'block' : 'none';
    chat.style.display = type === 'chat' ? 'block' : 'none';
    dm.style.display = type === 'dm' ? 'block' : 'none';
    warmup.style.display = type === 'warmup' ? 'block' : 'none';
    sequence.style.display = type === 'sequence' ? 'block' : 'none';
    scrape.style.display = type === 'scrape' ? 'block' : 'none';
    if (type === 'adder') refreshTaskGroups();
    if (type === 'dm') refreshTaskDMGroups();
}

async function refreshTaskDMGroups() {
    const d = await api('/api/groups');
    const sel = document.getElementById('taskDMGroupSelect');
    if (sel) {
        sel.innerHTML = '';
        (d.groups || []).forEach(g => { sel.innerHTML += `<option value="${g}">${g}</option>`; });
    }
}

async function startExtract() {
    const link = document.getElementById('extractLink').value.trim();
    if (!link) return showToast('请输入链接', 'warning');
    document.getElementById('extractLog').textContent = '';
    const include_keywords = (document.getElementById('extractInclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
    const exclude_keywords = (document.getElementById('extractExclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
    const auto_load = document.getElementById('extractAutoLoad').checked;
    const d = await api('/api/extract/start', 'POST', { link, include_keywords, exclude_keywords, auto_load });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function stopExtract() {
    const d = await api('/api/extract/stop', 'POST');
    showToast(d.message, d.status ? 'success' : 'error');
}

async function startBatchExtract() {
    const links = (document.getElementById('batchExtractLinks').value || '').split('\n').map(v => v.trim()).filter(Boolean);
    if (!links.length) return showToast('请输入链接', 'warning');
    document.getElementById('extractLog').textContent = '';
    const include_keywords = (document.getElementById('batchExtractInclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
    const exclude_keywords = (document.getElementById('batchExtractExclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
    const auto_load = document.getElementById('batchExtractAutoLoad').checked;
    const d = await api('/api/extract/batch', 'POST', { links, include_keywords, exclude_keywords, auto_load });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function removeGroup() {
    const name = document.getElementById('groupSelect').value;
    if (!name) return showToast('请选择群', 'warning');
    const d = await api('/api/group/remove', 'POST', { name });
    showToast(d.message, d.status ? 'success' : 'error');
    if (d.status) refreshGroups();
}

// ==================== Adder ====================
async function refreshLoadGroups() { refreshGroups(); }

async function loadMembers() {
    const name = document.getElementById('loadGroupSelect').value;
    if (!name) return showToast('请选择群', 'warning');
    const d = await api('/api/members/load', 'POST', { name });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function clearMembers() {
    const d = await api('/api/members/clear', 'POST');
    showToast(d.message, d.status ? 'success' : 'error');
}

async function startAdder() {
    const link = document.getElementById('adderLink').value.trim();
    const number_add = parseInt(document.getElementById('addsPerAccount').value);
    const number_account = parseInt(document.getElementById('numAccounts').value);
    if (!link) return showToast('请输入群ID', 'warning');
    document.getElementById('adderLog').textContent = '';
    const d = await api('/api/adder/start', 'POST', { link, number_add, number_account });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function stopAdder() {
    const d = await api('/api/adder/stop', 'POST');
    showToast(d.message, d.status ? 'success' : 'error');
}

async function startInvite() {
    const link = document.getElementById('inviteTarget').value.trim();
    const group_names = (document.getElementById('inviteGroupNames').value || '').split('\n').map(v => v.trim()).filter(Boolean);
    const number_add = parseInt(document.getElementById('inviteAddsPerAccount').value);
    const number_account = parseInt(document.getElementById('inviteNumAccounts').value);
    const use_loaded = document.getElementById('inviteUseLoaded').checked;
    if (!link) return showToast('请输入群ID', 'warning');
    const d = await api('/api/invite/start', 'POST', { link, group_names, number_add, number_account, use_loaded });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function startJoin() {
    const links = (document.getElementById('joinLinks').value || '').split('\n').map(v => v.trim()).filter(Boolean);
    const number_account = parseInt(document.getElementById('joinNumAccounts').value);
    if (!links.length) return showToast('请输入链接', 'warning');
    const d = await api('/api/join/start', 'POST', { links, number_account });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function startChat() {
    const link = document.getElementById('chatLink').value.trim();
    const messages = (document.getElementById('chatMessages').value || '').split('\n').map(v => v.trim()).filter(Boolean);
    const number_account = parseInt(document.getElementById('chatNumAccounts').value);
    const min_delay = parseInt(document.getElementById('chatMinDelay').value);
    const max_delay = parseInt(document.getElementById('chatMaxDelay').value);
    const max_messages = parseInt(document.getElementById('chatMaxMessages').value);
    if (!link) return showToast('请输入群链接', 'warning');
    if (!messages.length) return showToast('请输入消息', 'warning');
    const d = await api('/api/chat/start', 'POST', { link, messages, number_account, min_delay, max_delay, max_messages });
    showToast(d.message, d.status ? 'success' : 'error');
}

async function stopChat() {
    const d = await api('/api/chat/stop', 'POST');
    showToast(d.message, d.status ? 'success' : 'error');
}

function saveLog() {
    const log = document.getElementById('adderLog').textContent;
    if (!log) return showToast('没有日志', 'warning');
    const blob = new Blob([log], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = new Date().toISOString().slice(0,19).replace(/:/g,'-') + '_log.txt';
    a.click();
    showToast('日志已保存', 'success');
}

async function loadMe() {
    const d = await api('/api/auth/me');
    if (!d.user) return;
    const el = document.getElementById('userName');
    if (el) el.textContent = `${d.user.username} (${d.user.role})`;
    const adminTab = document.querySelector('[data-tab="admin"]');
    if (adminTab && d.user.role !== 'admin') adminTab.style.display = 'none';
}

async function logout() {
    await api('/api/auth/logout', 'POST');
    location.href = '/login';
}

async function loadSettings() {
    const d = await api('/api/settings');
    if (!d.settings) return;
    document.getElementById('settingMinDelay').value = d.settings.min_delay || 20;
    document.getElementById('settingMaxDelay').value = d.settings.max_delay || 100;
    document.getElementById('settingFloodLimit').value = d.settings.flood_wait_limit || 500;
    document.getElementById('settingMaxErrors').value = d.settings.max_errors || 5;
    document.getElementById('settingMaxMembers').value = d.settings.max_members_limit || 200;
    document.getElementById('settingMaxConcurrent').value = d.settings.max_concurrent || 0;
    document.getElementById('settingChatMin').value = d.settings.chat_interval_min || 15;
    document.getElementById('settingChatMax').value = d.settings.chat_interval_max || 45;
    document.getElementById('settingChatMessages').value = d.settings.chat_messages || '';
    document.getElementById('settingLang').value = d.settings.lang || 'zh';
    document.getElementById('settingDb1Host').value = d.settings.db1_host || '';
    document.getElementById('settingDb1Port').value = d.settings.db1_port || '3306';
    document.getElementById('settingDb1User').value = d.settings.db1_user || '';
    document.getElementById('settingDb1Pass').value = d.settings.db1_pass || '';
    document.getElementById('settingDb1Name').value = d.settings.db1_name || '';
    document.getElementById('settingDb2Host').value = d.settings.db2_host || '';
    document.getElementById('settingDb2Port').value = d.settings.db2_port || '3306';
    document.getElementById('settingDb2User').value = d.settings.db2_user || '';
    document.getElementById('settingDb2Pass').value = d.settings.db2_pass || '';
    document.getElementById('settingDb2Name').value = d.settings.db2_name || '';
}

async function saveSettings() {
    const settings = [
        ['min_delay', document.getElementById('settingMinDelay').value],
        ['max_delay', document.getElementById('settingMaxDelay').value],
        ['flood_wait_limit', document.getElementById('settingFloodLimit').value],
        ['max_errors', document.getElementById('settingMaxErrors').value],
        ['max_members_limit', document.getElementById('settingMaxMembers').value],
        ['max_concurrent', document.getElementById('settingMaxConcurrent').value],
        ['chat_interval_min', document.getElementById('settingChatMin').value],
        ['chat_interval_max', document.getElementById('settingChatMax').value],
        ['chat_messages', document.getElementById('settingChatMessages').value],
        ['lang', document.getElementById('settingLang').value],
        ['db1_host', document.getElementById('settingDb1Host').value],
        ['db1_port', document.getElementById('settingDb1Port').value],
        ['db1_user', document.getElementById('settingDb1User').value],
        ['db1_pass', document.getElementById('settingDb1Pass').value],
        ['db1_name', document.getElementById('settingDb1Name').value],
        ['db2_host', document.getElementById('settingDb2Host').value],
        ['db2_port', document.getElementById('settingDb2Port').value],
        ['db2_user', document.getElementById('settingDb2User').value],
        ['db2_pass', document.getElementById('settingDb2Pass').value],
        ['db2_name', document.getElementById('settingDb2Name').value]
    ];
    for (const [key, value] of settings) {
        await api('/api/settings/set', 'POST', { key, value });
    }
    showToast('设置已保存', 'success');
}

async function refreshApis() {
    const d = await api('/api/apis');
    const list = document.getElementById('apiList');
    list.innerHTML = '';
    (d.items || []).forEach(it => {
        const status = it.enabled ? '🟢' : '⚪';
        list.innerHTML += `<div class="list-item"><span>${status}</span><span>${it.api_id}</span><span class="muted">${it.api_hash}</span><button class="btn btn-sm" onclick="toggleApi(${it.id}, ${it.enabled ? 'false' : 'true'})"><i class="fas fa-toggle-${it.enabled ? 'on' : 'off'}"></i></button><button class="btn btn-sm" onclick="editApi(${it.id}, ${it.api_id}, '${it.api_hash}')"><i class="fas fa-pen"></i></button><button class="btn btn-sm" onclick="removeApi(${it.id})"><i class="fas fa-trash"></i></button></div>`;
    });
}

async function addApi() {
    const api_id = parseInt(document.getElementById('apiIdInput').value);
    const api_hash = document.getElementById('apiHashInput').value.trim();
    if (!api_id || !api_hash) return showToast('请输入API信息', 'warning');
    await api('/api/apis/add', 'POST', { api_id, api_hash });
    document.getElementById('apiIdInput').value = '';
    document.getElementById('apiHashInput').value = '';
    apiEditId = null;
    refreshApis();
}

async function removeApi(id) {
    await api('/api/apis/remove', 'POST', { id });
    refreshApis();
}

async function editApi(id, api_id, api_hash) {
    apiEditId = id;
    document.getElementById('apiIdInput').value = api_id;
    document.getElementById('apiHashInput').value = api_hash;
}

async function updateApi() {
    if (!apiEditId) return showToast('请选择要编辑的API', 'warning');
    const api_id = parseInt(document.getElementById('apiIdInput').value);
    const api_hash = document.getElementById('apiHashInput').value.trim();
    if (!api_id || !api_hash) return showToast('请输入API信息', 'warning');
    await api('/api/apis/update', 'POST', { id: apiEditId, api_id, api_hash });
    apiEditId = null;
    document.getElementById('apiIdInput').value = '';
    document.getElementById('apiHashInput').value = '';
    refreshApis();
}

async function toggleApi(id, enabled) {
    await api('/api/apis/toggle', 'POST', { id, enabled });
    refreshApis();
}

async function importApis() {
    const lines = document.getElementById('apiImportInput').value || '';
    if (!lines.trim()) return showToast('请输入API列表', 'warning');
    const d = await api('/api/apis/import', 'POST', { lines });
    showToast(`已导入 ${d.count || 0} 条`, 'success');
    document.getElementById('apiImportInput').value = '';
    refreshApis();
}

async function refreshProxies() {
    const d = await api('/api/proxies');
    const list = document.getElementById('proxyList');
    list.innerHTML = '';
    (d.items || []).forEach(it => {
        const status = it.ok === 1 ? '🟢' : it.ok === 0 ? '🔴' : '⚪';
        const enabled = it.enabled ? '🟢' : '⚪';
        const raw_url = it.raw_url || '';
        const short_url = raw_url.length > 30 ? raw_url.substring(0, 30) + '...' : raw_url;
        list.innerHTML += `<div class="list-item"><span>${enabled}</span><span>${status}</span><span title="${raw_url}">${short_url}</span><button class="btn btn-sm" onclick="toggleProxy(${it.id}, ${it.enabled ? 'false' : 'true'})"><i class="fas fa-toggle-${it.enabled ? 'on' : 'off'}"></i></button><button class="btn btn-sm" onclick="editProxy(${it.id}, '', '', 0, '', '', '${raw_url}')"><i class="fas fa-pen"></i></button><button class="btn btn-sm" onclick="testProxy(${it.id})"><i class="fas fa-vial"></i></button><button class="btn btn-sm" onclick="removeProxy(${it.id})"><i class="fas fa-trash"></i></button></div>`;
    });
}

async function addProxy() {
    const raw_url = document.getElementById('proxyRawUrl').value.trim();
    if (!raw_url) return showToast('请输入代理链接', 'warning');
    await api('/api/proxy/add', 'POST', { raw_url });
    document.getElementById('proxyRawUrl').value = '';
    proxyEditId = null;
    refreshProxies();
}

async function removeProxy(id) {
    await api('/api/proxies/remove', 'POST', { id });
    refreshProxies();
}

async function testProxy(id) {
    const d = await api('/api/proxies/test', 'POST', { id });
    showToast(d.ok ? '代理可用' : '代理不可用', d.ok ? 'success' : 'error');
    refreshProxies();
}

async function editProxy(id, scheme, host, port, username, password, raw_url) {
    proxyEditId = id;
    document.getElementById('proxyRawUrl').value = raw_url || '';
}

async function updateProxy() {
    if (!proxyEditId) return showToast('请选择要编辑的代理', 'warning');
    const raw_url = document.getElementById('proxyRawUrl').value.trim();
    if (!raw_url) return showToast('请输入代理链接', 'warning');
    await api('/api/proxy/update', 'POST', { id: proxyEditId, raw_url });
    proxyEditId = null;
    document.getElementById('proxyRawUrl').value = '';
    refreshProxies();
}

async function toggleProxy(id, enabled) {
    await api('/api/proxies/toggle', 'POST', { id, enabled });
    refreshProxies();
}

async function importProxies() {
    const lines = document.getElementById('proxyImportInput').value || '';
    if (!lines.trim()) return showToast('请输入代理列表', 'warning');
    const d = await api('/api/proxies/import', 'POST', { lines });
    showToast(`已导入 ${d.count || 0} 条`, 'success');
    document.getElementById('proxyImportInput').value = '';
    refreshProxies();
}

async function refreshLists() {
    const d = await api('/api/lists');
    const black = document.getElementById('blacklistList');
    const white = document.getElementById('whitelistList');
    black.innerHTML = '';
    white.innerHTML = '';
    (d.blacklist || []).forEach(v => {
        black.innerHTML += `<div class="list-item"><span>${v}</span><button class="btn btn-sm" onclick="removeList('blacklist','${v}')"><i class="fas fa-trash"></i></button></div>`;
    });
    (d.whitelist || []).forEach(v => {
        white.innerHTML += `<div class="list-item"><span>${v}</span><button class="btn btn-sm" onclick="removeList('whitelist','${v}')"><i class="fas fa-trash"></i></button></div>`;
    });
}

async function addBlacklist() {
    const value = document.getElementById('blacklistInput').value.trim();
    if (!value) return showToast('请输入账号', 'warning');
    await api('/api/lists/add', 'POST', { list_type: 'blacklist', value });
    document.getElementById('blacklistInput').value = '';
    refreshLists();
}

async function addWhitelist() {
    const value = document.getElementById('whitelistInput').value.trim();
    if (!value) return showToast('请输入账号', 'warning');
    await api('/api/lists/add', 'POST', { list_type: 'whitelist', value });
    document.getElementById('whitelistInput').value = '';
    refreshLists();
}

async function removeList(list_type, value) {
    await api('/api/lists/remove', 'POST', { list_type, value });
    refreshLists();
}

async function refreshUsers() {
    const d = await api('/api/users');
    const list = document.getElementById('userList');
    list.innerHTML = '';
    (d.items || []).forEach(u => {
        list.innerHTML += `<div class="list-item"><span>${u.username}</span><span class="muted">${u.role}</span><button class="btn btn-sm" onclick="removeUser(${u.id})"><i class="fas fa-trash"></i></button></div>`;
    });
}

async function addUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const role = document.getElementById('newRole').value;
    if (!username || !password) return showToast('请输入用户名和密码', 'warning');
    await api('/api/users/add', 'POST', { username, password, role });
    document.getElementById('newUsername').value = '';
    document.getElementById('newPassword').value = '';
    refreshUsers();
}

async function removeUser(id) {
    await api('/api/users/remove', 'POST', { id });
    refreshUsers();
}

async function refreshWorkers() {
    const d = await api('/api/workers');
    const list = document.getElementById('workerList');
    list.innerHTML = '';
    (d.items || []).forEach(w => {
        const time = w.last_seen ? new Date(w.last_seen * 1000).toLocaleString() : '';
        list.innerHTML += `<div class="list-item"><span>${w.name}</span><span class="muted">${w.status || ''}</span><span class="muted">${time}</span></div>`;
    });
}

async function refreshTasks() {
    const d = await api('/api/tasks');
    const list = document.getElementById('taskList');
    list.innerHTML = '';
    (d.items || []).forEach(t => {
        const runAt = new Date(t.run_at * 1000).toLocaleString();
        list.innerHTML += `<div class="list-item"><span>#${t.id}</span><span>${t.type}</span><span class="muted">${t.status}</span><span class="muted">${runAt}</span><button class="btn btn-sm" onclick="showTaskLog(${t.id})"><i class="fas fa-file-alt"></i></button><button class="btn btn-sm" onclick="stopTask(${t.id})"><i class="fas fa-stop"></i></button><button class="btn btn-sm" onclick="deleteTask(${t.id})"><i class="fas fa-trash"></i></button></div>`;
    });
}

async function createTask() {
    const type = document.getElementById('taskType').value;
    const runAtRaw = document.getElementById('taskRunAt').value;
    const run_at = runAtRaw ? Math.floor(new Date(runAtRaw).getTime() / 1000) : null;
    let payload = {};
    if (type === 'extract') {
        const link = document.getElementById('taskExtractLink').value.trim();
        if (!link) return showToast('请输入链接', 'warning');
        const include_keywords = (document.getElementById('taskExtractInclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const exclude_keywords = (document.getElementById('taskExtractExclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const auto_load = document.getElementById('taskExtractAutoLoad').checked;
        const use_remote_db = document.getElementById('taskExtractRemote').checked;
        payload = { link, include_keywords, exclude_keywords, auto_load, use_remote_db };
    } else if (type === 'extract_batch') {
        const links = (document.getElementById('taskBatchLinks').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        if (!links.length) return showToast('请输入链接', 'warning');
        const include_keywords = (document.getElementById('taskBatchInclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const exclude_keywords = (document.getElementById('taskBatchExclude').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const auto_load = document.getElementById('taskBatchAutoLoad').checked;
        const use_remote_db = document.getElementById('taskBatchRemote').checked;
        payload = { links, include_keywords, exclude_keywords, auto_load, use_remote_db };
    } else if (type === 'scrape') {
        const link = document.getElementById('taskScrapeLink').value.trim();
        const limit = parseInt(document.getElementById('taskScrapeLimit').value);
        const min_length = parseInt(document.getElementById('taskScrapeMinLength').value);
        const keywords_blacklist = (document.getElementById('taskScrapeBlacklist').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const save_to_remote = document.getElementById('taskScrapeRemote').checked;
        if (!link) return showToast('请输入链接', 'warning');
        payload = { link, limit, min_length, keywords_blacklist, save_to_remote };
    } else if (type === 'adder') {
        const link = document.getElementById('taskAdderLink').value.trim();
        const group_name = document.getElementById('taskGroupSelect').value;
        const number_add = parseInt(document.getElementById('taskAddsPerAccount').value);
        const number_account = parseInt(document.getElementById('taskNumAccounts').value);
        const use_remote_db = document.getElementById('taskAdderRemote').checked;
        if (!link) return showToast('请输入群ID', 'warning');
        payload = { link, group_name, number_add, number_account, use_remote_db };
    } else if (type === 'invite') {
        const link = document.getElementById('taskInviteLink').value.trim();
        const group_names = (document.getElementById('taskInviteGroups').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const number_add = parseInt(document.getElementById('taskInviteAddsPerAccount').value);
        const number_account = parseInt(document.getElementById('taskInviteNumAccounts').value);
        const use_remote_db = document.getElementById('taskInviteRemote').checked;
        if (!link) return showToast('请输入群ID', 'warning');
        payload = { link, group_names, number_add, number_account, use_remote_db };
    } else if (type === 'join') {
        const links = (document.getElementById('taskJoinLinks').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const number_account = parseInt(document.getElementById('taskJoinNumAccounts').value);
        if (!links.length) return showToast('请输入链接', 'warning');
        payload = { links, number_account };
    } else if (type === 'chat') {
        const link = document.getElementById('taskChatLink').value.trim();
        const messages = (document.getElementById('taskChatMessages').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const number_account = parseInt(document.getElementById('taskChatNumAccounts').value);
        const min_delay = parseInt(document.getElementById('taskChatMinDelay').value);
        const max_delay = parseInt(document.getElementById('taskChatMaxDelay').value);
        const max_messages = parseInt(document.getElementById('taskChatMaxMessages').value);
        const use_remote_db = document.getElementById('taskChatRemote').checked;
        if (!link) return showToast('请输入群链接', 'warning');
        if (!messages.length && !use_remote_db) return showToast('请输入消息', 'warning');
        payload = { link, messages, number_account, min_delay, max_delay, max_messages, use_remote_db };
    } else if (type === 'dm') {
        const group_name = document.getElementById('taskDMGroupSelect').value;
        const messages = (document.getElementById('taskDMMessages').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const min_delay = parseInt(document.getElementById('taskDMMinDelay').value);
        const max_delay = parseInt(document.getElementById('taskDMMaxDelay').value);
        const number_account = parseInt(document.getElementById('taskDMNumAccounts').value);
        if (!group_name) return showToast('请选择群', 'warning');
        if (!messages.length) return showToast('请输入消息', 'warning');
        payload = { group_name, messages, min_delay, max_delay, number_account };
    } else if (type === 'warmup') {
        const duration_min = parseInt(document.getElementById('taskWarmupDuration').value);
        const actions = (document.getElementById('taskWarmupActions').value || '').split(',').map(v => v.trim()).filter(Boolean);
        const number_account = parseInt(document.getElementById('taskWarmupNumAccounts').value);
        payload = { duration_min, actions, number_account };
    } else if (type === 'sequence') {
        const chat_link = document.getElementById('taskSequenceChatLink').value.trim();
        const messages = (document.getElementById('taskSequenceMessages').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const min_delay = parseInt(document.getElementById('taskSequenceMinDelay').value);
        const max_delay = parseInt(document.getElementById('taskSequenceMaxDelay').value);
        const chat_per_account = parseInt(document.getElementById('taskSequenceChatPerAccount').value);
        const pick_min = parseInt(document.getElementById('taskSequencePickMin').value);
        const pick_max = parseInt(document.getElementById('taskSequencePickMax').value);
        const add_link = document.getElementById('taskSequenceAddLink').value.trim();
        const group_names = (document.getElementById('taskSequenceGroupNames').value || '').split('\n').map(v => v.trim()).filter(Boolean);
        const use_loaded = document.getElementById('taskSequenceUseLoaded').checked;
        const adds_per_account = parseInt(document.getElementById('taskSequenceAddsPerAccount').value);
        const number_account = parseInt(document.getElementById('taskSequenceNumAccounts').value);
        const keep_online = document.getElementById('taskSequenceKeepOnline').checked;
        const use_remote_db = document.getElementById('taskSequenceAddRemote').checked;
        const use_remote_content = document.getElementById('taskSequenceChatRemote').checked;
        if (!chat_link) return showToast('请输入群链接', 'warning');
        if (!messages.length && !use_remote_content) return showToast('请输入消息', 'warning');
        if (!add_link) return showToast('请输入目标群ID', 'warning');
        payload = { chat_link, messages, min_delay, max_delay, chat_per_account, pick_min, pick_max, add_link, group_names, use_loaded, adds_per_account, number_account, keep_online, use_remote_db, use_remote_content };
    }
    const d = await api('/api/tasks/create', 'POST', { type, payload, run_at });
    showToast(d.status ? '任务已创建' : d.message, d.status ? 'success' : 'error');
    refreshTasks();
}

async function stopTask(id) {
    await api('/api/tasks/stop', 'POST', { id });
    refreshTasks();
}

async function showTaskLog(id) {
    const d = await api('/api/tasks/log', 'POST', { id });
    const log = d.log || '';
    if (!log) return showToast('没有日志', 'info');
    const blob = new Blob([log], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `task_${id}_log.txt`;
    a.click();
}

async function deleteTask(id) {
    await api('/api/tasks/delete', 'POST', { id });
    refreshTasks();
}

async function refreshReports() {
    const startRaw = document.getElementById('reportStart').value;
    const endRaw = document.getElementById('reportEnd').value;
    const start = startRaw ? Math.floor(new Date(startRaw).getTime() / 1000) : null;
    const end = endRaw ? Math.floor(new Date(endRaw).getTime() / 1000) : null;
    const params = new URLSearchParams();
    if (start) params.append('start', String(start));
    if (end) params.append('end', String(end));
    const d = await api(`/api/reports/summary${params.toString() ? '?' + params.toString() : ''}`);
    document.getElementById('reportAdded').textContent = d.added || 0;
    document.getElementById('reportTasksDone').textContent = d.tasks_done || 0;
    document.getElementById('reportTasksFailed').textContent = d.tasks_failed || 0;
}

function exportReports() {
    const startRaw = document.getElementById('reportStart').value;
    const endRaw = document.getElementById('reportEnd').value;
    const start = startRaw ? Math.floor(new Date(startRaw).getTime() / 1000) : null;
    const end = endRaw ? Math.floor(new Date(endRaw).getTime() / 1000) : null;
    const params = new URLSearchParams();
    if (start) params.append('start', String(start));
    if (end) params.append('end', String(end));
    window.open(`/api/reports/export${params.toString() ? '?' + params.toString() : ''}`, '_blank');
}

// ==================== Init ====================
document.addEventListener('DOMContentLoaded', () => {
    connectWS();
    loadMe();
    refreshAccounts();
    refreshGroups();
    refreshTasks();
    refreshReports();
    toggleTaskType();
});
