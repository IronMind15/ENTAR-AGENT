#!/bin/bash
# 恩特小助手 — 部署打包脚本
# 在项目根目录运行：bash deploy/pack.sh
# 会将需要上传的文件打包为 entar-agent.tar.gz

set -e

echo "📦 打包恩特小助手部署文件..."

TAR_FILE="entar-agent.tar.gz"

# 打包时只携带源码、依赖和部署配置。
# 运行数据（data/）、向量库（knowledge_base/）、日志和本地模型必须由
# 目标环境单独提供，防止把真实业务数据打进部署包。
tar -czf "$TAR_FILE" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="scripts/local_config.py" \
    --exclude="venv" \
    --exclude=".git" \
    --exclude=".claude" \
    --exclude="docs" \
    scripts/ \
    requirements.txt \
    deploy/ \
    .dockerignore

echo "✅ 打包完成：$TAR_FILE"
echo "   大小：$(du -h $TAR_FILE | cut -f1)"
echo ""
echo "   上传到服务器："
echo "   scp $TAR_FILE root@你的服务器IP:/opt/"
echo ""
echo "   在服务器上解压："
echo "   ssh root@你的服务器IP"
echo "   cd /opt && tar -xzf $TAR_FILE"
echo "   运行数据请单独挂载，并配置 ENTAR_RUNTIME_DIR / ENTAR_KNOWLEDGE_BASE_DIR"
