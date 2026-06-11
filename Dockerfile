FROM python:3.12-slim

WORKDIR /app/backend

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（使用清华镜像加速）
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制后端代码
COPY backend/ ./
# 复制前端静态文件
COPY frontend/ ../frontend/

# 数据目录 → 通过环境变量指向 /app/data，由 volume 挂载
RUN mkdir -p /app/data/uploads
ENV AVATAR_DB_PATH=/app/data/avatars.db
ENV UPLOAD_DIR=/app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
