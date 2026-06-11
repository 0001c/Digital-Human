"""
数字人视频生成工具 - FastAPI 后端服务
基于火山引擎"单图音频驱动"API 实现
"""

import asyncio
import json
import os
import traceback
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import MODE_CONFIG, POLL_INTERVAL, MAX_POLL_ATTEMPTS, PUBLIC_BASE_URL
from storage import get_store, COST_PER_AVATAR, COST_PER_VIDEO_SECOND
from volcengine_client import VolcengineClient

app = FastAPI(
    title="数字人视频生成工具",
    description="基于火山引擎单图音频驱动API的数字人视频生成服务",
    version="1.0.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建上传目录（可通过环境变量 UPLOAD_DIR 覆盖）
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 前端静态文件
FRONTEND_DIR = Path("../frontend")

# 初始化火山引擎客户端
client = VolcengineClient()


# ─── 请求/响应模型 ───────────────────────────────────────────────


class CreateAvatarRequest(BaseModel):
    image_url: str
    mode: str = "normal"
    label: str = ""       # 任务标签，便于检索
    notes: str = ""       # 任务备注


class GenerateVideoRequest(BaseModel):
    resource_id: str
    audio_url: str
    mode: str = "normal"
    audio_duration: Optional[float] = None
    label: str = ""
    notes: str = ""
    content_producer: Optional[str] = None
    producer_id: Optional[str] = None
    content_propagator: Optional[str] = None
    propagate_id: Optional[str] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    code: int
    message: str
    data: Optional[dict] = None


# ─── API 路由 ───────────────────────────────────────────────────


@app.get("/api/modes")
async def get_modes():
    """获取可用的驱动模式列表及说明"""
    return {
        "code": 10000,
        "data": MODE_CONFIG,
    }


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "数字人视频生成工具"}


# ─── 动态凭据管理 ────────────────────────────────────────────────

class SettingsRequest(BaseModel):
    access_key: str
    secret_key: str


# 运行时凭据存储（内存中，重启后需重新配置）
_runtime_credentials: dict = {}

# 任务元数据临时存储（用于计费时获取音频时长等）
_task_meta: dict = {}


@app.post("/api/settings")
async def save_settings(req: SettingsRequest):
    """前端设置面板保存 AK/SK"""
    global _runtime_credentials
    if not req.access_key or not req.secret_key:
        raise HTTPException(400, "AK 和 SK 不能为空")

    _runtime_credentials = {
        "access_key": req.access_key,
        "secret_key": req.secret_key,
    }
    client.update_credentials(req.access_key, req.secret_key)
    return {"code": 10000, "message": "凭据已保存"}


@app.get("/api/settings/status")
async def settings_status():
    """检查 AK/SK 是否已配置（不泄露密钥内容）"""
    key = _runtime_credentials.get("access_key") or client.access_key
    return {
        "code": 10000,
        "data": {
            "configured": bool(key),
            "source": "frontend" if _runtime_credentials else ("env" if client.access_key else "none"),
        },
    }


def _check_credentials():
    """检查凭据是否已配置，未配置则抛出异常"""
    if not client.is_configured():
        raise HTTPException(
            401,
            "请先配置火山引擎 AK/SK。点击页面右上角齿轮图标进行设置。"
        )


@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...)):
    """上传图片文件，返回访问URL"""
    try:
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(400, "仅支持图片文件")

        ext = Path(file.filename).suffix if file.filename else ".png"
        filename = f"img_{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        url = PUBLIC_BASE_URL + f"/uploads/{filename}" if PUBLIC_BASE_URL else f"/uploads/{filename}"
        return {
            "code": 10000,
            "message": "上传成功",
            "data": {
                "filename": filename,
                "url": url,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上传失败: {str(e)}")


@app.post("/api/upload/audio")
async def upload_audio(file: UploadFile = File(...)):
    """上传音频文件，返回访问URL"""
    try:
        allowed_types = ("audio/", "video/")
        if not file.content_type or not any(
            file.content_type.startswith(t) for t in allowed_types
        ):
            raise HTTPException(400, "仅支持音频/视频文件")

        ext = Path(file.filename).suffix if file.filename else ".mp3"
        filename = f"audio_{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        url = PUBLIC_BASE_URL + f"/uploads/{filename}" if PUBLIC_BASE_URL else f"/uploads/{filename}"
        return {
            "code": 10000,
            "message": "上传成功",
            "data": {
                "filename": filename,
                "url": url,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上传失败: {str(e)}")


@app.post("/api/avatar/create")
async def create_avatar(req: CreateAvatarRequest):
    """步骤1: 提交形象创建任务"""
    _check_credentials()
    if req.mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {req.mode}，可选值: {list(MODE_CONFIG.keys())}")

    try:
        result = await client.create_avatar(image_url=req.image_url, mode=req.mode)
        code = result.get("code", -1)

        if code == 10000:
            task_id = result["data"]["task_id"]
            # 保存任务记录
            store = get_store()
            store.save_task(
                access_key=client.access_key,
                task_id=task_id,
                task_type="avatar",
                mode=req.mode,
                label=req.label,
                notes=req.notes,
                image_url=req.image_url,
            )
            return {
                "code": 10000,
                "message": "形象创建任务已提交",
                "data": {
                    "task_id": task_id,
                },
            }
        else:
            return {
                "code": code,
                "message": result.get("message", "提交失败"),
                "data": None,
            }

    except Exception as e:
        raise HTTPException(500, f"请求火山引擎API失败: {str(e)}")


@app.get("/api/avatar/status")
async def query_avatar_status(mode: str, task_id: str, image_url: Optional[str] = None):
    """查询形象创建任务状态（单次查询，由前端负责轮询节奏）。成功后自动保存形象记录。"""
    if mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {mode}")

    try:
        result = await client.query_avatar(mode=mode, task_id=task_id)
        code = result.get("code", -1)
        data = result.get("data", {})

        if code != 10000:
            return {
                "code": code,
                "message": result.get("message", "查询失败"),
                "data": None,
            }

        status = data.get("status", "")

        # 实时更新任务状态
        try:
            store = get_store()
            store.update_task_status(
                access_key=client.access_key,
                task_id=task_id,
                status=status,
            )
        except Exception:
            pass

        if status == "done":
            # 解析 resp_data 获取 resource_id
            resp_data_str = data.get("resp_data", "{}")
            resp_data = {}
            try:
                resp_data = json.loads(resp_data_str)
                resource_id = resp_data.get("resource_id", "")
            except json.JSONDecodeError:
                resource_id = ""

            # 更新任务状态为 done，附带 resource_id
            try:
                store = get_store()
                store.update_task_status(
                    access_key=client.access_key,
                    task_id=task_id,
                    status="done",
                    resource_id=resource_id,
                )
            except Exception:
                pass

            # 自动保存形象记录 + 计费
            saved_record = None
            if resource_id:
                try:
                    store = get_store()
                    saved_record = store.save_avatar(
                        resource_id=resource_id,
                        mode=mode,
                        image_url=image_url or "",
                        task_id=task_id,
                        face_position=resp_data.get("face_position"),
                        role_type=resp_data.get("role_type"),
                    )
                    store.record_cost(
                        access_key=client.access_key,
                        operation_type="avatar_create",
                        cost=COST_PER_AVATAR,
                        unit_price=COST_PER_AVATAR,
                        quantity=1,
                        resource_id=resource_id,
                        task_id=task_id,
                        description=f"形象创建 - {mode} 模式",
                    )
                except Exception:
                    # traceback.print_exc()
                    pass

            return {
                "code": 10000,
                "message": "形象创建完成",
                "data": {
                    "task_id": task_id,
                    "status": "done",
                    "resource_id": resource_id,
                    "saved_record": saved_record,
                },
            }

        # 非终态：直接返回当前状态，由前端继续轮询
        return {
            "code": 10000,
            "message": result.get("message", "处理中"),
            "data": {
                "task_id": task_id,
                "status": status,
                "resource_id": None,
            },
        }

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@app.get("/api/avatar/status/poll")
async def poll_avatar_status(mode: str, task_id: str):
    """查询形象创建任务状态（单次查询），完成后自动保存记录和计费"""
    if mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {mode}")

    try:
        result = await client.query_avatar(mode=mode, task_id=task_id)
        code = result.get("code", -1)
        data = result.get("data", {})

        status = data.get("status", "")
        resource_id = None
        resp_data = {}

        if status == "done":
            try:
                resp_data = json.loads(data.get("resp_data", "{}"))
                resource_id = resp_data.get("resource_id", "")
            except (json.JSONDecodeError, TypeError):
                pass

            # 保存形象记录 + 计费
            if resource_id:
                try:
                    store = get_store()
                    # 从 tasks 表获取 image_url
                    tasks = store.search_tasks(client.access_key, task_id, limit=1)
                    task_info = tasks[0] if tasks else {}
                    store.save_avatar(
                        resource_id=resource_id,
                        mode=mode,
                        image_url=task_info.get("image_url", ""),
                        task_id=task_id,
                        face_position=resp_data.get("face_position"),
                        role_type=resp_data.get("role_type"),
                    )
                    store.record_cost(
                        access_key=client.access_key,
                        operation_type="avatar_create",
                        cost=COST_PER_AVATAR,
                        unit_price=COST_PER_AVATAR,
                        quantity=1,
                        resource_id=resource_id,
                        task_id=task_id,
                        description=f"形象创建 - {mode} 模式",
                    )
                except Exception:
                    pass

        return {
            "code": code,
            "message": result.get("message", ""),
            "data": {
                "task_id": task_id,
                "status": status,
                "resource_id": resource_id,
            },
        }

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@app.post("/api/video/generate")
async def generate_video(req: GenerateVideoRequest):
    """步骤2: 提交视频生成任务"""
    _check_credentials()
    if req.mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {req.mode}，可选值: {list(MODE_CONFIG.keys())}")

    # 构建隐式水印标识
    aigc_meta = None
    if req.content_producer or req.content_propagator:
        aigc_meta = {
            "content_producer": req.content_producer or "",
            "producer_id": req.producer_id or "",
            "content_propagator": req.content_propagator or "",
            "propagate_id": req.propagate_id or "",
        }

    try:
        result = await client.generate_video(
            resource_id=req.resource_id,
            audio_url=req.audio_url,
            mode=req.mode,
            aigc_meta=aigc_meta,
        )
        code = result.get("code", -1)

        if code == 10000:
            task_id = result["data"]["task_id"]
            # 暂存音频时长供后续计费使用
            _task_meta[task_id] = {
                "audio_duration": req.audio_duration,
                "audio_url": req.audio_url,
                "resource_id": req.resource_id,
                "mode": req.mode,
            }
            # 保存任务记录
            store = get_store()
            store.save_task(
                access_key=client.access_key,
                task_id=task_id,
                task_type="video",
                mode=req.mode,
                label=req.label,
                notes=req.notes,
                resource_id=req.resource_id,
                audio_url=req.audio_url,
                audio_duration=req.audio_duration or 0,
            )
            return {
                "code": 10000,
                "message": "视频生成任务已提交",
                "data": {
                    "task_id": task_id,
                },
            }
        else:
            return {
                "code": code,
                "message": result.get("message", "提交失败"),
                "data": None,
            }

    except Exception as e:
        raise HTTPException(500, f"请求火山引擎API失败: {str(e)}")


@app.get("/api/video/status")
async def query_video_status(
    mode: str,
    task_id: str,
    resource_id: Optional[str] = None,
    content_producer: Optional[str] = None,
    producer_id: Optional[str] = None,
    content_propagator: Optional[str] = None,
    propagate_id: Optional[str] = None,
):
    """查询视频生成任务状态（单次查询，由前端负责轮询节奏）。成功后自动更新形象关联视频信息。"""
    if mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {mode}")

    # 构建隐式水印标识
    aigc_meta = None
    if content_producer or content_propagator:
        aigc_meta = {
            "content_producer": content_producer or "",
            "producer_id": producer_id or "",
            "content_propagator": content_propagator or "",
            "propagate_id": propagate_id or "",
        }

    try:
        result = await client.query_video(
            mode=mode, task_id=task_id, aigc_meta=aigc_meta
        )
        code = result.get("code", -1)
        data = result.get("data", {})

        if code != 10000:
            return {
                "code": code,
                "message": result.get("message", "查询失败"),
                "data": None,
            }

        status = data.get("status", "")

        # 实时更新任务状态
        try:
            store = get_store()
            store.update_task_status(
                access_key=client.access_key,
                task_id=task_id,
                status=status,
            )
        except Exception:
            pass

        if status == "done":
            # 解析视频URL
            video_urls = []
            video_meta = None
            resp_data_str = data.get("resp_data", "{}")
            try:
                resp_data = json.loads(resp_data_str)
                preview_urls = resp_data.get("preview_url", [])
                if preview_urls:
                    video_urls.extend(preview_urls)
                video_meta = resp_data.get("video", {})
            except json.JSONDecodeError:
                pass

            # 大画幅灵动模式有直接的 video_url
            direct_video_url = data.get("video_url")
            if direct_video_url:
                video_urls.append(direct_video_url)

            # 自动更新形象记录 + 计费
            if resource_id and video_urls:
                try:
                    store = get_store()
                    store.update_video_info(
                        resource_id=resource_id,
                        video_url=video_urls[0] if video_urls else None,
                        video_meta=video_meta,
                    )
                    # 记录视频生成费用
                    meta = _task_meta.pop(task_id, {})
                    audio_duration = meta.get("audio_duration", 0) or 0
                    if audio_duration > 0:
                        video_cost = round(audio_duration * COST_PER_VIDEO_SECOND, 2)
                    else:
                        video_cost = 0
                    store.record_cost(
                        access_key=client.access_key,
                        operation_type="video_generate",
                        cost=video_cost,
                        unit_price=COST_PER_VIDEO_SECOND,
                        quantity=audio_duration,
                        resource_id=resource_id,
                        task_id=task_id,
                        description=(
                            f"视频生成 - {meta.get('mode', mode)} 模式"
                            + (f"（{audio_duration}秒）" if audio_duration > 0 else "（未获取时长）")
                        ),
                    )
                    # 自动保存视频生成记录（用于历史查看）
                    store.save_video_record(
                        access_key=client.access_key,
                        resource_id=resource_id,
                        task_id=task_id,
                        mode=mode,
                        video_urls=video_urls,
                        audio_url=meta.get("audio_url"),
                        audio_duration=audio_duration,
                    )
                except Exception:
                    # traceback.print_exc()
                    pass

            return {
                "code": 10000,
                "message": "视频生成完成",
                "data": {
                    "task_id": task_id,
                    "status": "done",
                    "video_urls": video_urls,
                    "aigc_meta_tagged": data.get("aigc_meta_tagged"),
                },
            }

        # 非终态：直接返回当前状态，由前端继续轮询
        return {
            "code": 10000,
            "message": result.get("message", "处理中"),
            "data": {
                "task_id": task_id,
                "status": status,
                "video_urls": [],
            },
        }

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@app.get("/api/video/status/poll")
async def poll_video_status(
    mode: str,
    task_id: str,
    content_producer: Optional[str] = None,
    producer_id: Optional[str] = None,
    content_propagator: Optional[str] = None,
    propagate_id: Optional[str] = None,
):
    """查询视频生成任务状态（单次查询），完成后自动保存记录和计费"""
    if mode not in MODE_CONFIG:
        raise HTTPException(400, f"无效的模式: {mode}")

    aigc_meta = None
    if content_producer or content_propagator:
        aigc_meta = {
            "content_producer": content_producer or "",
            "producer_id": producer_id or "",
            "content_propagator": content_propagator or "",
            "propagate_id": propagate_id or "",
        }

    try:
        result = await client.query_video(
            mode=mode, task_id=task_id, aigc_meta=aigc_meta
        )
        code = result.get("code", -1)
        data = result.get("data", {})
        status = data.get("status", "")

        video_urls = []
        video_meta = None
        try:
            resp_data = json.loads(data.get("resp_data", "{}"))
            preview_urls = resp_data.get("preview_url", [])
            if preview_urls:
                video_urls.extend(preview_urls)
            video_meta = resp_data.get("video", {})
        except json.JSONDecodeError:
            pass

        direct_video_url = data.get("video_url")
        if direct_video_url:
            video_urls.append(direct_video_url)

        # 完成后自动保存视频记录和计费
        if status == "done" and video_urls:
            try:
                store = get_store()
                # 从 tasks 表获取 resource_id 等信息
                tasks = store.search_tasks(client.access_key, task_id, limit=1)
                task_info = tasks[0] if tasks else {}
                resource_id = task_info.get("resource_id", "")

                if resource_id:
                    store.update_video_info(
                        resource_id=resource_id,
                        video_url=video_urls[0] if video_urls else None,
                        video_meta=video_meta,
                    )
                    audio_duration = task_info.get("audio_duration", 0) or 0
                    video_cost = round(audio_duration * COST_PER_VIDEO_SECOND, 2) if audio_duration > 0 else 0
                    store.record_cost(
                        access_key=client.access_key,
                        operation_type="video_generate",
                        cost=video_cost,
                        unit_price=COST_PER_VIDEO_SECOND,
                        quantity=audio_duration,
                        resource_id=resource_id,
                        task_id=task_id,
                        description=f"视频生成 - {mode} 模式" + (f"（{audio_duration}秒）" if audio_duration > 0 else ""),
                    )
                    store.save_video_record(
                        access_key=client.access_key,
                        resource_id=resource_id,
                        task_id=task_id,
                        mode=mode,
                        video_urls=video_urls,
                        audio_url=task_info.get("audio_url"),
                        audio_duration=audio_duration,
                    )
            except Exception:
                # traceback.print_exc()
                pass

        return {
            "code": code,
            "message": result.get("message", ""),
            "data": {
                "task_id": task_id,
                "status": status,
                "video_urls": video_urls,
                "aigc_meta_tagged": data.get("aigc_meta_tagged"),
            },
        }

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


# ─── 历史形象记录 API ───────────────────────────────────────────


@app.get("/api/avatars")
async def list_avatars(mode: Optional[str] = None, limit: int = 50, offset: int = 0):
    """获取历史形象列表，支持按模式筛选"""
    store = get_store()
    records = store.list_avatars(mode=mode, limit=limit, offset=offset)
    return {
        "code": 10000,
        "data": {
            "avatars": records,
            "total": len(records),
        },
    }


@app.get("/api/avatars/{resource_id}")
async def get_avatar(resource_id: str):
    """获取单个形象详情"""
    store = get_store()
    record = store.get_avatar(resource_id)
    if not record:
        raise HTTPException(404, "形象记录不存在")
    return {"code": 10000, "data": record}


@app.delete("/api/avatars/{resource_id}")
async def delete_avatar(resource_id: str):
    """删除一条形象记录"""
    store = get_store()
    deleted = store.delete_avatar(resource_id)
    if not deleted:
        raise HTTPException(404, "形象记录不存在")
    return {"code": 10000, "message": "删除成功"}


@app.get("/api/avatars/search")
async def search_avatars(keyword: str, limit: int = 20):
    """按关键字搜索历史形象"""
    store = get_store()
    records = store.search_avatars(keyword, limit=limit)
    return {
        "code": 10000,
        "data": {
            "avatars": records,
            "total": len(records),
        },
    }


# ─── 费用统计 API ───────────────────────────────────────────────


@app.get("/api/billing/summary")
async def billing_summary():
    """获取当前 AK 账户的总花费统计"""
    _check_credentials()
    store = get_store()
    summary = store.get_cost_summary(client.access_key)
    return {
        "code": 10000,
        "data": summary,
    }


# ─── 视频生成记录 API ───────────────────────────────────────────

class UpdateRecordMetaRequest(BaseModel):
    tags: Optional[list] = None
    notes: Optional[str] = None


@app.get("/api/video-records")
async def list_video_records(
    tag: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
):
    """获取当前 AK 的视频生成记录列表，支持标签筛选和关键字搜索"""
    _check_credentials()
    store = get_store()
    records = store.list_video_records(
        access_key=client.access_key,
        tag=tag,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    all_tags = store.get_all_tags(client.access_key)
    return {
        "code": 10000,
        "data": {"records": records, "tags": all_tags},
    }


@app.get("/api/video-records/{record_id}")
async def get_video_record(record_id: int):
    """获取单条视频记录详情"""
    _check_credentials()
    store = get_store()
    record = store.get_video_record(client.access_key, record_id)
    if not record:
        raise HTTPException(404, "记录不存在或不属于当前账户")
    return {"code": 10000, "data": record}


@app.put("/api/video-records/{record_id}")
async def update_video_record(record_id: int, req: UpdateRecordMetaRequest):
    """更新视频记录的标签和备注"""
    _check_credentials()
    store = get_store()
    updated = store.update_video_record_meta(
        access_key=client.access_key,
        record_id=record_id,
        tags=req.tags,
        notes=req.notes,
    )
    if not updated:
        raise HTTPException(404, "记录不存在或不属于当前账户")
    return {"code": 10000, "message": "更新成功", "data": updated}


@app.delete("/api/video-records/{record_id}")
async def delete_video_record(record_id: int):
    """删除一条视频记录"""
    _check_credentials()
    store = get_store()
    deleted = store.delete_video_record(client.access_key, record_id)
    if not deleted:
        raise HTTPException(404, "记录不存在或不属于当前账户")
    return {"code": 10000, "message": "删除成功"}


@app.get("/api/video-records/tags/all")
async def all_tags():
    """获取当前 AK 所有已使用的标签"""
    _check_credentials()
    store = get_store()
    tags = store.get_all_tags(client.access_key)
    return {"code": 10000, "data": tags}


# ─── 任务跟踪 API ────────────────────────────────────────────────


@app.get("/api/tasks")
async def list_tasks(
    label: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """获取任务列表，支持按标签/状态/类型筛选，或按关键字搜索"""
    _check_credentials()
    store = get_store()

    if keyword:
        records = store.search_tasks(client.access_key, keyword, limit)
        labels = []
    else:
        records = store.list_tasks(
            access_key=client.access_key,
            label=label,
            status=status,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )
        labels = store.get_task_labels(client.access_key)

    return {
        "code": 10000,
        "data": {"tasks": records, "labels": labels},
    }


@app.get("/api/tasks/pending")
async def pending_tasks():
    """获取所有未完成的任务（用于后台轮询刷新状态）"""
    _check_credentials()
    store = get_store()
    records = store.get_pending_tasks(client.access_key)
    return {"code": 10000, "data": {"tasks": records}}


@app.delete("/api/tasks/{task_id}")
async def delete_task_endpoint(task_id: str):
    """删除一条任务记录"""
    _check_credentials()
    store = get_store()
    deleted = store.delete_task(client.access_key, task_id)
    if not deleted:
        raise HTTPException(404, "任务不存在或不属于当前账户")
    return {"code": 10000, "message": "删除成功"}


@app.post("/api/tasks/refresh-status")
async def refresh_task_statuses():
    """批量刷新所有未完成任务的最新状态，完成后自动保存记录和计费"""
    _check_credentials()
    store = get_store()
    pending = store.get_pending_tasks(client.access_key)

    updated = []
    for task in pending:
        try:
            task_id = task["task_id"]
            mode = task.get("mode", "normal")
            task_type = task["task_type"]

            if task_type == "avatar":
                result = await client.query_avatar(mode=mode, task_id=task_id)
            else:
                result = await client.query_video(mode=mode, task_id=task_id)

            code = result.get("code", -1)
            data = result.get("data", {})
            new_status = data.get("status", task["status"])

            if new_status != task["status"] or new_status == "done":
                resource_id = task.get("resource_id", "")

                if new_status == "done":
                    if task_type == "avatar":
                        # 解析 resource_id
                        try:
                            resp_data = json.loads(data.get("resp_data", "{}"))
                            resource_id = resp_data.get("resource_id", "")
                        except (json.JSONDecodeError, TypeError):
                            pass

                        # 保存形象记录 + 计费
                        if resource_id:
                            try:
                                face_pos = resp_data.get("face_position") if resp_data else None
                                store.save_avatar(
                                    resource_id=resource_id,
                                    mode=mode,
                                    image_url=task.get("image_url", ""),
                                    task_id=task_id,
                                    face_position=face_pos,
                                )
                                store.record_cost(
                                    access_key=client.access_key,
                                    operation_type="avatar_create",
                                    cost=COST_PER_AVATAR,
                                    unit_price=COST_PER_AVATAR,
                                    quantity=1,
                                    resource_id=resource_id,
                                    task_id=task_id,
                                    description=f"形象创建 - {mode} 模式（任务面板）",
                                )
                            except Exception:
                                # traceback.print_exc()
                                pass

                    else:  # video
                        # 解析视频 URL
                        video_urls = []
                        video_meta = None
                        try:
                            resp_data = json.loads(data.get("resp_data", "{}"))
                            preview_urls = resp_data.get("preview_url", [])
                            if preview_urls:
                                video_urls.extend(preview_urls)
                            video_meta = resp_data.get("video", {})
                        except (json.JSONDecodeError, TypeError):
                            pass
                        direct_video_url = data.get("video_url")
                        if direct_video_url:
                            video_urls.append(direct_video_url)

                        if resource_id and video_urls:
                            try:
                                store.update_video_info(
                                    resource_id=resource_id,
                                    video_url=video_urls[0] if video_urls else None,
                                    video_meta=video_meta,
                                )
                                audio_duration = task.get("audio_duration", 0) or 0
                                video_cost = round(audio_duration * COST_PER_VIDEO_SECOND, 2) if audio_duration > 0 else 0
                                store.record_cost(
                                    access_key=client.access_key,
                                    operation_type="video_generate",
                                    cost=video_cost,
                                    unit_price=COST_PER_VIDEO_SECOND,
                                    quantity=audio_duration,
                                    resource_id=resource_id,
                                    task_id=task_id,
                                    description=f"视频生成 - {mode} 模式（任务面板）" + (f"（{audio_duration}秒）" if audio_duration > 0 else ""),
                                )
                                store.save_video_record(
                                    access_key=client.access_key,
                                    resource_id=resource_id,
                                    task_id=task_id,
                                    mode=mode,
                                    video_urls=video_urls,
                                    audio_url=task.get("audio_url"),
                                    audio_duration=audio_duration,
                                )
                            except Exception:
                                # traceback.print_exc()
                                pass

                store.update_task_status(
                    access_key=client.access_key,
                    task_id=task_id,
                    status=new_status,
                    resource_id=resource_id,
                )
                task["status"] = new_status
                task["resource_id"] = resource_id or task.get("resource_id", "")
                updated.append(task)

        except Exception:
            pass

    return {
        "code": 10000,
        "data": {"updated_tasks": [dict(t) for t in updated]},
    }


# ─── 静态文件服务 ────────────────────────────────────────────────


@app.get("/")
async def serve_frontend():
    """提供前端页面"""
    frontend_path = FRONTEND_DIR / "index.html"
    if frontend_path.exists():
        return FileResponse(frontend_path)
    return JSONResponse({"message": "前端文件未找到，请确保 frontend/index.html 存在"})


# 挂载上传目录作为静态文件服务
if UPLOAD_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


# ─── 启动入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
