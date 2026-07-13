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

# 先装 CPU 版 PyTorch（~400MB），再装其他依赖
# sentence-transformers 依赖 torch，若 torch 已存在则不会重新拉 GPU 版
# 注：用 --index-url 临时切换 PyTorch 官方源，不被清华源覆盖
# 安装系统依赖（unstructured 需要 libmagic、poppler 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 poppler-utils && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# 再复制项目文件
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY knowledge_base/ ./knowledge_base/

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "scripts/main.py"]
