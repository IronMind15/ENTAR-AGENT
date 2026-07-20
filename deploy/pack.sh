#!/bin/bash
# 恩特小助手 — 部署打包脚本
# 在项目根目录运行：bash deploy/pack.sh
# 会将需要上传的文件打包为 entar-agent.tar.gz

set -e

echo "📦 打包恩特小助手部署文件..."

TAR_FILE="entar-agent.tar.gz"

# 打包时排除：__pycache__、local_config.py（密钥）、venv（虚拟环境）
tar -czf "$TAR_FILE" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="scripts/local_config.py" \
    --exclude="venv" \
    --exclude=".git" \
    --exclude=".claude" \
    --exclude="docs" \
    scripts/ \
    data/ \
    knowledge_base/ \
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
