FROM python:3.11-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# 安装 Xray-core
ARG XRAY_VERSION=1.8.6
RUN curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip" \
    -o /tmp/xray.zip && \
    unzip /tmp/xray.zip -d /usr/local/bin/ && \
    chmod +x /usr/local/bin/xray && \
    rm /tmp/xray.zip

# 工作目录
WORKDIR /app

# 先安装依赖（利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建日志和配置目录
RUN mkdir -p /app/logs /app/xray_configs

# 环境变量默认值
ENV PORT=8000 \
    XRAY_BIN_PATH=/usr/local/bin/xray \
    XRAY_CONFIG_DIR=/app/xray_configs \
    LOG_DIR=/app/logs

EXPOSE ${PORT}

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# 启动
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
