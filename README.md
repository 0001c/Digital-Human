# Digital Human

基于火山引擎「智能数字人」API 的一站式数字人形象创建与视频生成工具，支持形象管理、视频合成、任务监控、费用统计与历史记录管理。

> **注意**：本项目目前处于 **Demo 阶段**，已实现核心功能模块。关于详细的技术实现、API 调用方式及参数说明，请以 [火山引擎官方接入文档](https://www.volcengine.com/docs/86081/1804512?lang=zh) 为准。
>
> [关于智能视觉部分服务逐步下线的预通知--图像生成大模型-火山引擎](https://docs.volcengine.com/docs/86081/2488927?lang=zh&_vtm_=a106466.b106468.0_0.0_0.0.85_7649680111349089826)
>
> 因官方模型参数限制，目前存在问题：资源需公网可访问，本地无法直接上传资源，使用内网穿透可解决
>
> 仅供学习交流，如有问题欢迎指正\~

***

## 核心功能

- **形象创建**：支持标准模式和灵动模式（大画幅/小画幅），上传人像图片生成数字人形象
- **视频生成**：支持上传本地音频 / 输入音频 URL，结合已创建形象合成视频
- **历史形象管理**：查看、复制已创建的形象 resource\_id，快速复用
- **任务面板**：非阻塞式任务提交，实时状态监控，支持按标签/备注检索历史任务
- **视频记录**：历史视频生成记录查看，自定义标签与备注，按标签/关键字搜索
- **费用统计**：按 AK 隔离的累计花费、形象创建及视频生成次数统计
- **多账户**：通过 AK/SK 切换账户，数据隔离

***

## 技术栈

| 层   | 技术                             |
| --- | ------------------------------ |
| 后端  | Python 3.12 + FastAPI + SQLite |
| 前端  | 原生 HTML/CSS/JavaScript         |
| 容器化 | Docker + Docker Compose        |

***

## 项目结构

```
数字人/
├── backend/
│   ├── app.py                  # FastAPI 主入口，API 路由
│   ├── config.py               # 配置（环境变量、模式定义）
│   ├── storage.py              # SQLite 数据库操作层
│   ├── volcengine_client.py    # 火山引擎 API 客户端
│   └── requirements.txt        # Python 依赖
├── frontend/
│   └── index.html              # 前端 SPA 页面
├── Dockerfile                  # Docker 镜像构建
├── docker-compose.yml          # 本地 Docker 部署
├── docker-compose.nas.yml      # NAS（镜像导入）部署
├── start.bat                   # Windows 一键启动脚本
└── .gitignore
```

***

## 环境要求

### 本地运行

- Python 3.10+
- Windows / macOS / Linux

### Docker 部署

- Docker 20.10+

### 火山引擎

- 已开通 [火山引擎智能数字人服务](https://www.volcengine.com/docs/86081/1804512?lang=zh)
- 获取 **Access Key (AK)** 和 **Secret Key (SK) **[访问控制-火山引擎](https://console.volcengine.com/iam/keymanage)

***

## 快速开始

### 方式一：start.bat 一键启动（Windows）

双击项目根目录下的 [start.bat](start.bat)，自动完成虚拟环境创建、依赖安装与服务启动。

### 方式二：手动启动

```bash
# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux

# 2. 安装依赖
pip install -r backend/requirements.txt

# 3. 启动服务
cd backend
python app.py
```

访问 `http://localhost:8000`，在页面右上角设置中填入火山引擎 AK/SK 即可使用。

***

## Docker 部署

```bash
# 构建并启动
docker-compose up -d --build

# 访问
http://localhost:8000
```

数据（数据库 + 上传文件）通过 volume `digital-human-data` 持久化，容器重建不丢失。

### NAS 部署

```bash
# 1. 构建并导出镜像（在开发机上）
docker build -t digital-human .
docker save -o digital-human-image.tar digital-human

# 2. 将 digital-human-deploy.tar 上传到 NAS，解压
tar -xvf digital-human-deploy.tar

# 3. 导入镜像并启动
docker load -i digital-human-image.tar
docker-compose -f docker-compose.nas.yml up -d
```

***

## 环境变量

| 变量                | 默认值                    | 说明                                           |
| ----------------- | ---------------------- | -------------------------------------------- |
| `VOLC_ACCESS_KEY` | `""`                   | 火山引擎 Access Key（可在 Web 设置面板动态配置）             |
| `VOLC_SECRET_KEY` | `""`                   | 火山引擎 Secret Key（可在 Web 设置面板动态配置）             |
| `PUBLIC_BASE_URL` | `""`                   | 公网访问地址，用于生成上传文件的完整 URL 供火山引擎 API 下载。留空返回相对路径 |
| `AVATAR_DB_PATH`  | `/app/data/avatars.db` | SQLite 数据库文件路径                               |
| `UPLOAD_DIR`      | `/app/data/uploads`    | 上传文件存储目录                                     |

***

## API 端点概览

| 端点                          | 方法   | 说明          |
| --------------------------- | ---- | ----------- |
| `/api/upload/image`         | POST | 上传人像图片      |
| `/api/upload/audio`         | POST | 上传音频文件      |
| `/api/avatar/create`        | POST | 创建数字人形象     |
| `/api/avatar/list`          | GET  | 获取形象列表      |
| `/api/video/generate`       | POST | 生成视频        |
| `/api/video/status`         | GET  | 查询视频任务状态    |
| `/api/tasks`                | GET  | 查询任务列表      |
| `/api/tasks/refresh-status` | POST | 批量刷新任务状态    |
| `/api/video-records`        | GET  | 查询视频生成记录    |
| `/api/settings`             | POST | 更新 AK/SK 配置 |
| `/api/settings/status`      | GET  | 查询配置状态      |
| `/api/billing`              | GET  | 获取费用统计      |

***

## 注意事项

1. **Demo 阶段**：本项目实现了核心功能闭环，但尚未经过完整生产环境验证，请勿直接用于关键业务。
2. **文件上传**：火山引擎 API 要求图片和音频 URL 必须为公网可访问地址。本地部署时需配置 frp/ngrok 内网穿透，并通过 `PUBLIC_BASE_URL` 环境变量设置公网地址。
3. **数据隔离**：以 AK 的 SHA256 哈希作为数据隔离键，不同 AK 账户之间数据完全独立。
4. **技术参考**：API 参数、响应格式、速率限制等细节请以 [火山引擎智能数字人开放接口文档](https://www.volcengine.com/docs/86081/1804512?lang=zh) 为准。
5. **费用**：具体计费标准请查阅火山引擎官方定价信息，本工具中的计费统计仅供参考。
6. 仅供学习交流。

