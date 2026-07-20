#!/bin/bash
# ==========================================
# 恩特小助手 — 一键部署脚本
# 用法：在项目目录下运行 bash deploy.sh
# ==========================================

set -e

echo "📦 恩特小助手部署脚本"
echo "========================"

# 1. 查找 tar 包（支持当前目录或 deploy/ 下）
TAR_FILE=""
if [ -f "entar-agent.tar.gz" ]; then
    TAR_FILE="entar-agent.tar.gz"
elif [ -f "deploy/entar-agent.tar.gz" ]; then
    TAR_FILE="deploy/entar-agent.tar.gz"
else
    echo "❌ 未找到 entar-agent.tar.gz，请先上传到项目目录"
    exit 1
fi

# 2. 解压代码（不覆盖 data/ 和 knowledge_base/）
echo "📂 解压代码..."
tar -xzf "$TAR_FILE" \
    --exclude="data/*" \
    --exclude="knowledge_base/*" \
    scripts/ deploy/ requirements.txt .dockerignore 2>/dev/null || true

# 3. 重建 Docker 镜像并重启
echo "🐳 重建 Docker 镜像..."
docker compose -f deploy/docker-compose.yml up -d --build

# 4. 检查状态
echo "✅ 部署完成！检查容器状态："
docker ps --filter name=entar-agent --format "table {{.ID}}\t{{.Status}}\t{{.CreatedAt}}"

echo ""
echo "📋 查看日志：docker compose -f deploy/docker-compose.yml logs -f"