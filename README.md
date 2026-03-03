# Telegram 多账号管理系统

一站式 Telegram 多账号运营平台，支持批量采集、邀请、群发、养号、限制检测，内置智能调度与风控。

---

## 功能模块

| 模块 | 功能 |
|------|------|
| **设置** | Supabase 远程数据库、Telegram API 配置池、代理管理（VLESS/VMess/Trojan） |
| **账号管理** | 多账号登录（验证码/2FA）、Session 持久化、智能指纹分配、养号、限制检测 |
| **采集** | 群成员采集（过滤管理员/机器人/在线状态）、群消息采集（关键词黑名单） |
| **操作** | 批量邀请入群、批量群发消息、多账号并发、智能账号选择 |
| **定时任务** | Cron 表达式调度、自动执行、进度追踪、失败重试、中途取消 |
| **日志** | 按模块/级别/任务/账号过滤、实时 SSE 推送、分页查询 |
| **风控** | 每日限额、FloodWait 冷却升级、PeerFlood 强制冷却、健康评分、新号保护期 |

## 技术栈

- **后端**: Python 3.11 + FastAPI + SQLAlchemy async + asyncpg
- **前端**: Jinja2 + Tailwind CSS (CDN) + Alpine.js + HTMX
- **Telegram**: Telethon (MTProto) + StringSession
- **数据库**: PostgreSQL + Supabase (可选远程同步)
- **代理**: Xray-core v1.8.6（VLESS/VMess/Trojan → SOCKS5）
- **调度**: APScheduler (AsyncIO)
- **部署**: Docker / Railway / docker-compose

---

## 项目结构

```
├── app/
│   ├── main.py                  # FastAPI 入口 + 生命周期
│   ├── config.py                # 配置 & 120 设备指纹库
│   ├── models.py                # SQLAlchemy ORM 模型
│   ├── database.py              # 数据库连接池 (pool=30, overflow=70)
│   ├── schemas.py               # Pydantic 模型
│   ├── routers/                 # API 路由
│   │   ├── settings.py          # 设置管理
│   │   ├── accounts.py          # 账号管理 + 智能指纹
│   │   ├── scraping.py          # 采集操作
│   │   ├── operations.py        # 邀请/群发 + 智能选号
│   │   ├── tasks.py             # 定时任务 CRUD
│   │   └── logs.py              # 日志查询 + SSE
│   ├── services/                # 业务逻辑
│   │   ├── proxy_manager.py     # Xray 进程管理
│   │   ├── telegram_client.py   # Telethon 客户端池 (LRU)
│   │   ├── account_service.py   # 养号 + 限制检测
│   │   ├── account_scheduler.py # 智能调度 + 风控
│   │   ├── circuit_breaker.py   # 熔断器
│   │   ├── scrape_service.py    # 采集服务
│   │   ├── invite_service.py    # 邀请服务
│   │   ├── chat_service.py      # 群发服务
│   │   ├── sync_service.py      # Supabase 同步
│   │   ├── session_restorer.py  # Session 恢复
│   │   └── task_scheduler.py    # APScheduler 调度
│   ├── templates/               # Jinja2 模板
│   └── static/                  # CSS + JS
├── Dockerfile
├── docker-compose.yml
├── railway.toml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone https://github.com/YOUR_USERNAME/telegram-manager.git
cd telegram-manager

# 复制环境变量
cp .env.example .env
# 编辑 .env，填入 DATABASE_URL 等配置
```

### 2. 本地开发运行

```bash
# 安装依赖
pip install -r requirements.txt

# 确保 PostgreSQL 已运行，DATABASE_URL 配置正确
# 启动应用（首次启动会自动创建表）
uvicorn app.main:app --reload --port 8000
```

访问 `http://localhost:8000`

### 3. 配置流程

1. **设置 > Telegram API** → 添加 `api_id` / `api_hash`（从 https://my.telegram.org 获取）
2. **设置 > 代理**（可选）→ 粘贴 VLESS/VMess/Trojan 链接
3. **设置 > Supabase**（可选）→ 配置远程数据库 URL 和 Key
4. **账号管理** → 输入手机号登录 Telegram 账号
5. **采集** → 输入群组链接采集成员/消息
6. **操作** → 执行批量邀请或群发

---

## 部署教程

### 方式一：Railway 部署（推荐）

Railway 提供免费额度，一键部署，自动 HTTPS。

#### 步骤 1：上传到 GitHub

```bash
# 初始化 Git 仓库
cd telegram-manager
git init
git add .
git commit -m "Initial commit"

# 创建 GitHub 仓库（使用 GitHub CLI 或网页创建）
gh repo create telegram-manager --private --push
# 或手动推送：
git remote add origin https://github.com/YOUR_USERNAME/telegram-manager.git
git branch -M main
git push -u origin main
```

#### 步骤 2：Railway 创建项目

1. 注册/登录 [Railway](https://railway.app)
2. 点击 **New Project** → **Deploy from GitHub repo**
3. 选择你的 `telegram-manager` 仓库
4. Railway 会自动检测 `Dockerfile` 并开始构建

#### 步骤 3：添加 PostgreSQL

1. 在 Railway 项目中点击 **+ New** → **Database** → **PostgreSQL**
2. Railway 会自动注入 `DATABASE_URL` 环境变量到你的服务
3. **重要**：Railway 的 `DATABASE_URL` 格式是 `postgresql://`，需要修改为 `postgresql+asyncpg://`

在 Railway 服务的 **Variables** 中添加：
```
DATABASE_URL=postgresql+asyncpg://用户名:密码@主机:端口/数据库名
```
（将 Railway PostgreSQL 提供的连接串 `postgresql://` 替换为 `postgresql+asyncpg://`）

#### 步骤 4：配置环境变量

在 Railway 服务的 **Variables** 页面设置：

| 变量 | 说明 | 必填 |
|------|------|------|
| `DATABASE_URL` | PostgreSQL 连接串（`postgresql+asyncpg://...`） | ✅ |
| `SECRET_KEY` | 随机安全密钥 | ✅ |
| `PORT` | 端口（Railway 自动设置，通常 8000） | 自动 |
| `SUPABASE_URL` | Supabase 项目 URL | 可选 |
| `SUPABASE_KEY` | Supabase anon key | 可选 |

#### 步骤 5：部署完成

- Railway 自动构建并部署
- 访问 Railway 分配的域名（`xxx.railway.app`）
- 健康检查路径：`/health`（超时 60 秒）

#### 后续更新

```bash
git add .
git commit -m "Update"
git push
# Railway 自动重新部署
```

---

### 方式二：Docker 部署

#### 使用 docker-compose（推荐）

```bash
# 复制配置
cp .env.example .env
# 编辑 .env 文件

# 启动服务（应用 + PostgreSQL）
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止
docker-compose down
```

访问 `http://localhost:8000`

#### 手动 Docker 部署

```bash
# 构建镜像
docker build -t telegram-manager .

# 运行（需要外部 PostgreSQL）
docker run -d \
  --name telegram-manager \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/dbname" \
  -e SECRET_KEY="your-secret-key" \
  -v telegram-logs:/app/logs \
  telegram-manager
```

---

### 方式三：其他 Docker 平台

#### Fly.io

```bash
# 安装 flyctl
curl -L https://fly.io/install.sh | sh

# 登录
fly auth login

# 创建应用
fly launch --name telegram-manager --no-deploy

# 创建 PostgreSQL
fly postgres create --name telegram-db

# 附加数据库
fly postgres attach telegram-db --app telegram-manager

# 设置环境变量（注意 DATABASE_URL 需改为 asyncpg 格式）
fly secrets set SECRET_KEY="your-secret-key"

# 部署
fly deploy
```

#### Render

1. 在 [Render](https://render.com) 创建 **Web Service**
2. 连接 GitHub 仓库
3. Environment 选择 **Docker**
4. 添加 PostgreSQL 数据库
5. 设置环境变量：`DATABASE_URL`（改为 `postgresql+asyncpg://` 前缀）、`SECRET_KEY`

#### DigitalOcean App Platform

1. 在 [DigitalOcean](https://www.digitalocean.com) 创建 **App**
2. 选择 GitHub 仓库，组件类型选 **Web Service**
3. 添加 **Dev Database**（PostgreSQL）
4. 环境变量设置同上

---

## GitHub 上传指南

```bash
# 1. 确保已安装 Git
git --version

# 2. 在项目目录初始化
cd telegram-manager
git init

# 3. 创建 .gitignore
cat > .gitignore << 'EOF'
__pycache__/
*.pyc
.env
*.db
logs/
xray_configs/
.vscode/
.idea/
EOF

# 4. 添加所有文件
git add .
git commit -m "feat: Telegram multi-account management system"

# 5. 在 GitHub 网页创建新仓库（不要初始化 README）
# 然后推送：
git remote add origin https://github.com/YOUR_USERNAME/telegram-manager.git
git branch -M main
git push -u origin main
```

---

## 系统架构

### 100 账号扩展特性

- **智能指纹分配**: 120 种设备指纹自动分配，避免重复
- **健康评分系统**: 0-100 分，FloodWait -5, PeerFlood -20
- **冷却升级策略**: FloodWait 1次→服务端时间, 2次→2h, 3次+→24h
- **每日限额**: 邀请/消息独立计数，午夜自动重置
- **新号保护**: 7 天内不执行高风险操作
- **智能选号**: 按健康分 DESC + 最后使用时间 ASC 排序
- **LRU 客户端池**: 自动回收空闲连接，防止内存溢出
- **数据库连接池**: pool_size=30, max_overflow=70，支持高并发

### 风控机制

| 事件 | 处理 |
|------|------|
| FloodWait (第1次) | 冷却 = Telegram 返回时间 |
| FloodWait (第2次) | 冷却 2 小时 |
| FloodWait (第3次+) | 冷却 24 小时 |
| PeerFloodError | 冷却 24 小时 + 健康分 -20 |
| 新号 (7天内) | 跳过邀请/群发任务 |

---

## 注意事项

1. **Telegram API**: 需自行在 https://my.telegram.org 申请 `api_id` 和 `api_hash`
2. **代理**: 如需翻墙，支持 VLESS/VMess/Trojan 协议，内置 Xray-core 转换
3. **风控**: 请合理设置邀请/群发间隔（建议 ≥300 秒），避免账号被封
4. **数据备份**: 建议配置 Supabase 远程同步，防止数据丢失
5. **安全**: 生产环境请设置强 `SECRET_KEY`，不要暴露 `.env` 文件

## License

MIT
