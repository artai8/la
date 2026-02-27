#!/bin/bash
set -e

echo "============================================"
echo "  Telegram Adder Panel - Starting..."
echo "============================================"

# 确保目录存在
mkdir -p account data delete downloads gaps

# 检查必要文件
if [ ! -f "api.txt" ]; then
    echo "WARNING: api.txt not found, creating template..."
    echo "ApiID:ApiHash" > api.txt
fi

if [ ! -f "proxy.txt" ]; then
    echo "WARNING: proxy.txt not found, creating template..."
    echo "ip:port:username:password" > proxy.txt
fi

echo "  Server starting on port ${PORT:-8080}"
echo "  Open http://localhost:${PORT:-8080}"
echo "============================================"

# 启动服务
exec python main.py
