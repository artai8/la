/* ========== 全局工具函数 ========== */

// Toast 通知
function showToast(message, type = 'info', duration = 3000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, duration);
}

// HTMX 全局配置
document.addEventListener('DOMContentLoaded', () => {
    // HTMX 事件监听
    document.body.addEventListener('htmx:afterSwap', (e) => {
        // 处理 swap 后的初始化
    });

    document.body.addEventListener('htmx:responseError', (e) => {
        showToast('请求失败: ' + e.detail.xhr.status, 'error');
    });

    document.body.addEventListener('htmx:sendError', () => {
        showToast('网络连接失败', 'error');
    });

    // 高亮当前导航
    highlightNav();
});

// 高亮当前页面的导航链接
function highlightNav() {
    const path = window.location.pathname;
    document.querySelectorAll('.nav-link, .nav-sub').forEach(link => {
        link.classList.remove('active');
        const href = link.getAttribute('href');
        if (href && path.startsWith(href) && href !== '/') {
            link.classList.add('active');
        }
    });
}

// 通用 fetch 封装
async function apiFetch(url, options = {}) {
    const defaults = {
        headers: { 'Content-Type': 'application/json' },
    };
    const config = { ...defaults, ...options };
    if (config.body && typeof config.body === 'object' && !(config.body instanceof FormData)) {
        config.body = JSON.stringify(config.body);
    }
    if (config.body instanceof FormData) {
        delete config.headers['Content-Type'];
    }

    try {
        const resp = await fetch(url, config);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// 确认删除对话框
function confirmDelete(message = '确定删除？') {
    return confirm(message);
}

// 格式化日期
function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 复制到剪贴板
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        showToast('已复制到剪贴板', 'success');
    } catch (e) {
        showToast('复制失败', 'error');
    }
}
