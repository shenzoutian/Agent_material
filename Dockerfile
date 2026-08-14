# litdiscovery —— 一键运行容器
#   构建：docker build -t litdiscovery .
#   运行：docker compose run --rm litdiscovery run --requirement "..." --auto
#   交互：docker run --rm -it --env-file .env -v ./artifacts:/app/artifacts litdiscovery

FROM python:3.12-slim

# 系统运行库：onnxruntime(Docling PDF 解析) 需 libgomp1；ca-certificates 供 HTTPS 下载
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用 Docker 层缓存（依赖未变时无需重复下载）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 装入源码并安装为可编辑包（提供 litdiscovery 命令入口）
COPY src ./src
COPY tools ./tools
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e .

# 产物目录挂载点（原始文献 / 运行产物，不入镜像）
VOLUME ["/app/artifacts"]

ENTRYPOINT ["litdiscovery"]
CMD ["--help"]
