"""
火山引擎 API 客户端
实现火山引擎 API V4 签名认证及视觉智能服务调用
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx

from config import (
    VOLC_ACCESS_KEY,
    VOLC_SECRET_KEY,
    API_HOST,
    API_ENDPOINT,
    REGION,
    SERVICE,
)


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return _hmac_sha256(key, msg)


def _get_signed_headers(
    method: str,
    uri: str,
    query: str,
    headers: dict,
    body: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
) -> dict:
    """生成火山引擎 API V4 签名的请求头"""
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    # Step 1: 创建规范请求
    canonical_headers = f"content-type:{headers.get('content-type', 'application/json')}\nhost:{API_HOST}\nx-date:{x_date}\n"
    signed_headers = "content-type;host;x-date"
    payload_hash = _sha256_hex(body)

    canonical_request = (
        f"{method}\n{uri}\n{query}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    # Step 2: 创建待签名字符串
    credential_scope = f"{short_date}/{region}/{service}/request"
    hashed_canonical_request = _sha256_hex(canonical_request)
    string_to_sign = f"HMAC-SHA256\n{x_date}\n{credential_scope}\n{hashed_canonical_request}"

    # Step 3: 计算签名
    k_date = _sign(secret_key.encode("utf-8"), short_date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # Step 4: 构造 Authorization 头
    authorization = (
        f"HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    signed_headers_dict = {
        "Content-Type": "application/json",
        "Host": API_HOST,
        "X-Date": x_date,
        "Authorization": authorization,
    }
    return signed_headers_dict


class VolcengineClient:
    """火山引擎视觉智能服务客户端"""

    def __init__(self):
        self.access_key = VOLC_ACCESS_KEY
        self.secret_key = VOLC_SECRET_KEY
        self.endpoint = API_ENDPOINT

    def update_credentials(self, access_key: str, secret_key: str):
        """运行时动态更新 AK/SK（来自前端设置面板）"""
        self.access_key = access_key
        self.secret_key = secret_key

    def is_configured(self) -> bool:
        """检查 AK/SK 是否已配置"""
        return bool(self.access_key and self.secret_key)

    async def _request(self, action: str, body: dict) -> dict:
        """发送签名请求到火山引擎 API"""
        method = "POST"
        uri = "/"
        query = f"Action={action}&Version=2022-08-31"

        headers = {
            "content-type": "application/json",
        }
        body_str = json.dumps(body, separators=(",", ":"))

        # 生成签名头
        signed_headers = _get_signed_headers(
            method=method,
            uri=uri,
            query=query,
            headers=headers,
            body=body_str,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=REGION,
            service=SERVICE,
        )

        url = f"{self.endpoint}?{query}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=signed_headers, content=body_str)
            return response.json()

    async def submit_task(self, req_key: str, **kwargs) -> dict:
        """提交任务 (形象创建 / 视频生成)"""
        body = {"req_key": req_key, **kwargs}
        return await self._request("CVSubmitTask", body)

    async def get_result(self, req_key: str, task_id: str, req_json: dict = None) -> dict:
        """查询任务结果"""
        body = {"req_key": req_key, "task_id": task_id}
        if req_json:
            body["req_json"] = json.dumps(req_json, separators=(",", ":"))
        return await self._request("CVGetResult", body)

    async def create_avatar(self, image_url: str, mode: str) -> dict:
        """步骤1: 创建数字形象"""
        from config import MODE_CONFIG

        mode_cfg = MODE_CONFIG[mode]
        return await self.submit_task(
            req_key=mode_cfg["create_role_req_key"],
            image_url=image_url,
        )

    async def query_avatar(self, mode: str, task_id: str) -> dict:
        """查询形象创建结果"""
        from config import MODE_CONFIG

        mode_cfg = MODE_CONFIG[mode]
        return await self.get_result(
            req_key=mode_cfg["create_role_req_key"],
            task_id=task_id,
        )

    async def generate_video(
        self, resource_id: str, audio_url: str, mode: str, aigc_meta: dict = None
    ) -> dict:
        """步骤2: 生成视频"""
        from config import MODE_CONFIG

        mode_cfg = MODE_CONFIG[mode]
        return await self.submit_task(
            req_key=mode_cfg["video_req_key"],
            resource_id=resource_id,
            audio_url=audio_url,
        )

    async def query_video(self, mode: str, task_id: str, aigc_meta: dict = None) -> dict:
        """查询视频生成结果"""
        from config import MODE_CONFIG

        mode_cfg = MODE_CONFIG[mode]
        req_json = None
        if aigc_meta:
            req_json = {"aigc_meta": aigc_meta}
        return await self.get_result(
            req_key=mode_cfg["video_req_key"],
            task_id=task_id,
            req_json=req_json,
        )
