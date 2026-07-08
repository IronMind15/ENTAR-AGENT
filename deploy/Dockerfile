# 恩特小助手 — Docker 镜像
# 基于 Python 3.11-slim，轻量且兼容

FROM python:3.11-slim

# 国内镜像加速（可根据云服务器地域调整）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV HF_ENDPOINT=https://hf-mirror.com

# 设置工作目录
WORKDIR /app

# 先复制依赖文件 → 安装依赖（利用 Docker 缓存层，代码不变时不重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制项目文件
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY knowledge_base/ ./knowledge_base/

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "scripts/main.py"]
