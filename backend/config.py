"""
火山引擎数字人视频生成 - 配置文件
使用前请填写您的火山引擎 AK/SK
"""

import os

# 火山引擎 API 认证信息
# 可通过前端设置面板动态配置，或通过环境变量设置
# 环境变量优先级低于前端配置（前端配置覆盖环境变量）
VOLC_ACCESS_KEY = os.environ.get("VOLC_ACCESS_KEY", "")
VOLC_SECRET_KEY = os.environ.get("VOLC_SECRET_KEY", "")

# 火山引擎视觉智能服务配置
API_HOST = "visual.volcengineapi.com"
API_ENDPOINT = f"https://{API_HOST}"
REGION = "cn-north-1"
SERVICE = "cv"
API_VERSION = "2022-08-31"

# 模式对应的 req_key 映射 
MODE_CONFIG = {
    "normal": {
        "name": "普通模式",
        "description": "驱动范围：嘴部 | 支持真人/动漫/宠物 | 最长音频180秒 | 原图比例输出",
        "create_role_req_key": "realman_avatar_picture_create_role",
        "video_req_key": "realman_avatar_picture_v2",
    },
    "loopy": {
        "name": "灵动模式",
        "description": "驱动范围：全脸 | 支持真人/动漫/宠物 | 真人最长180秒, 宠物90秒 | 512x512方形输出",
        "create_role_req_key": "realman_avatar_picture_create_role_loopy",
        "video_req_key": "realman_avatar_picture_loopy",
    },
    "loopy_large": {
        "name": "大画幅灵动模式",
        "description": "驱动范围：全脸+膝盖以上身体 | 仅支持真人/动漫 | 最长45秒音频 | 16:9/9:16/3:4/4:3",
        "create_role_req_key": "realman_avatar_picture_create_role_loopyb",
        "video_req_key": "realman_avatar_picture_loopyb",
    },
}

# 公网访问地址（用于构造上传文件的完整URL，供火山引擎API下载）
# 例如: http://mefrp_tunnel.atxa.top:16040
# 留空则返回相对路径
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

# 轮询配置
POLL_INTERVAL = 3  # 查询任务状态的间隔（秒）
MAX_POLL_ATTEMPTS = 200  # 最大轮询次数（约10分钟）
