"""
数字人形象历史记录存储模块
使用 SQLite 持久化已创建的数字形象信息，支持增删查操作
同时支持按 AK 账户记录费用统计
"""

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import os

DB_PATH = Path(os.environ.get("AVATAR_DB_PATH", str(Path(__file__).parent / "avatars.db")))

# 计费单价（来自火山引擎官方文档）
COST_PER_AVATAR = 0.1       # 形象创建：0.1 元/形象（失败不收费）
COST_PER_VIDEO_SECOND = 0.3 # 视频生成：0.3 元/秒（失败不收费）


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AvatarStore:
    """数字人形象历史记录存储器（线程安全）"""

    def __init__(self, db_path: str = None):
        self._db_path = str(db_path or DB_PATH)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS avatars (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        resource_id     TEXT NOT NULL UNIQUE,
                        mode            TEXT NOT NULL,
                        image_url       TEXT NOT NULL,
                        task_id         TEXT NOT NULL,
                        face_position   TEXT,
                        role_type       TEXT,
                        video_url       TEXT,
                        video_meta      TEXT,
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    )
                """)
                # 为常用查询创建索引
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_avatars_mode
                    ON avatars(mode)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_avatars_created_at
                    ON avatars(created_at DESC)
                """)
                # 计费记录表
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS billing (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        ak_hash         TEXT NOT NULL,
                        operation_type  TEXT NOT NULL,
                        resource_id     TEXT,
                        task_id         TEXT,
                        cost            REAL NOT NULL,
                        unit_price      REAL NOT NULL,
                        quantity        REAL NOT NULL,
                        description     TEXT,
                        created_at      TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_billing_ak_hash
                    ON billing(ak_hash)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_billing_created_at
                    ON billing(created_at DESC)
                """)
                # 视频生成历史记录表
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS video_records (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        ak_hash         TEXT NOT NULL,
                        resource_id     TEXT NOT NULL,
                        task_id         TEXT NOT NULL,
                        audio_url       TEXT,
                        audio_duration  REAL DEFAULT 0,
                        mode            TEXT NOT NULL,
                        video_urls      TEXT,
                        tags            TEXT DEFAULT '[]',
                        notes           TEXT DEFAULT '',
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vr_ak_hash
                    ON video_records(ak_hash)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vr_created_at
                    ON video_records(created_at DESC)
                """)
                # 任务跟踪表（支持异步提交 + 标签备注 + 进度查询）
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        ak_hash         TEXT NOT NULL,
                        task_id         TEXT NOT NULL,
                        task_type       TEXT NOT NULL,
                        resource_id     TEXT,
                        label           TEXT DEFAULT '',
                        notes           TEXT DEFAULT '',
                        status          TEXT DEFAULT 'submitted',
                        mode            TEXT,
                        image_url       TEXT,
                        audio_url       TEXT,
                        audio_duration  REAL DEFAULT 0,
                        error_message   TEXT,
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_ak_hash
                    ON tasks(ak_hash)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_created_at
                    ON tasks(created_at DESC)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tasks_label
                    ON tasks(label)
                """)
                conn.commit()
            finally:
                conn.close()

    def save_avatar(
        self,
        resource_id: str,
        mode: str,
        image_url: str,
        task_id: str,
        face_position: Optional[list] = None,
        role_type: Optional[str] = None,
    ) -> dict:
        """保存一条形象创建记录，如果 resource_id 已存在则更新"""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            try:
                existing = conn.execute(
                    "SELECT id FROM avatars WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()

                face_pos_json = json.dumps(face_position, ensure_ascii=False) if face_position else None

                if existing:
                    conn.execute(
                        """UPDATE avatars
                           SET mode=?, image_url=?, task_id=?, face_position=?,
                               role_type=?, updated_at=?
                           WHERE resource_id=?""",
                        (mode, image_url, task_id, face_pos_json,
                         role_type, now, resource_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO avatars
                           (resource_id, mode, image_url, task_id, face_position,
                            role_type, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (resource_id, mode, image_url, task_id, face_pos_json,
                         role_type, now, now),
                    )
                conn.commit()

                return self._row_to_dict(
                    conn.execute(
                        "SELECT * FROM avatars WHERE resource_id = ?",
                        (resource_id,),
                    ).fetchone()
                )
            finally:
                conn.close()

    def update_video_info(
        self,
        resource_id: str,
        video_url: Optional[str] = None,
        video_meta: Optional[dict] = None,
    ) -> Optional[dict]:
        """更新形象关联的视频信息"""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            try:
                video_meta_json = json.dumps(video_meta, ensure_ascii=False) if video_meta else None
                conn.execute(
                    """UPDATE avatars
                       SET video_url=COALESCE(?, video_url),
                           video_meta=COALESCE(?, video_meta),
                           updated_at=?
                       WHERE resource_id=?""",
                    (video_url, video_meta_json, now, resource_id),
                )
                conn.commit()

                row = conn.execute(
                    "SELECT * FROM avatars WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def list_avatars(self, mode: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[dict]:
        """查询历史形象列表，按创建时间倒序"""
        with self._lock:
            conn = self._get_conn()
            try:
                if mode:
                    rows = conn.execute(
                        "SELECT * FROM avatars WHERE mode=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (mode, limit, offset),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM avatars ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_avatar(self, resource_id: str) -> Optional[dict]:
        """根据 resource_id 获取单条记录"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM avatars WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def get_avatar_by_id(self, pk: int) -> Optional[dict]:
        """根据主键 ID 获取单条记录"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM avatars WHERE id = ?",
                    (pk,),
                ).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def delete_avatar(self, resource_id: str) -> bool:
        """删除一条形象记录"""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM avatars WHERE resource_id = ?",
                    (resource_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def search_avatars(self, keyword: str, limit: int = 20) -> list[dict]:
        """按关键字搜索（匹配 resource_id 或 image_url）"""
        with self._lock:
            conn = self._get_conn()
            try:
                kw = f"%{keyword}%"
                rows = conn.execute(
                    """SELECT * FROM avatars
                       WHERE resource_id LIKE ? OR image_url LIKE ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (kw, kw, limit),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _ak_hash(access_key: str) -> str:
        """对 AK 做哈希，用于按账户分组统计（不存储明文 AK）"""
        return hashlib.sha256(access_key.encode()).hexdigest()

    def record_cost(
        self,
        access_key: str,
        operation_type: str,
        cost: float,
        unit_price: float,
        quantity: float,
        resource_id: Optional[str] = None,
        task_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """记录一条费用"""
        now = _now_iso()
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    """INSERT INTO billing
                       (ak_hash, operation_type, resource_id, task_id, cost,
                        unit_price, quantity, description, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ak_hash, operation_type, resource_id, task_id, cost,
                     unit_price, quantity, description, now),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM billing WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                return dict(row)
            finally:
                conn.close()

    def get_cost_summary(self, access_key: str) -> dict:
        """查询当前 AK 的总花费及分类统计"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                total = conn.execute(
                    "SELECT COALESCE(SUM(cost), 0) AS total FROM billing WHERE ak_hash = ?",
                    (ak_hash,),
                ).fetchone()["total"]

                breakdown = conn.execute(
                    """SELECT operation_type,
                              COUNT(*) AS count,
                              COALESCE(SUM(cost), 0) AS total_cost
                       FROM billing
                       WHERE ak_hash = ?
                       GROUP BY operation_type
                       ORDER BY total_cost DESC""",
                    (ak_hash,),
                ).fetchall()

                record_count = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM billing WHERE ak_hash = ?",
                    (ak_hash,),
                ).fetchone()["cnt"]

                return {
                    "total_cost": round(total, 2),
                    "avatar_create_count": sum(
                        r["count"] for r in breakdown if r["operation_type"] == "avatar_create"
                    ),
                    "video_generate_count": sum(
                        r["count"] for r in breakdown if r["operation_type"] == "video_generate"
                    ),
                    "record_count": record_count,
                    "breakdown": [
                        {
                            "type": r["operation_type"],
                            "count": r["count"],
                            "total_cost": round(r["total_cost"], 2),
                        }
                        for r in breakdown
                    ],
                }
            finally:
                conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict:
        if row is None:
            return None
        d = dict(row)
        # 反序列化 JSON 字段
        for field in ("face_position", "video_meta"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    # ─── 视频生成记录 ─────────────────────────────────────────

    def save_video_record(
        self,
        access_key: str,
        resource_id: str,
        task_id: str,
        mode: str,
        video_urls: list,
        audio_url: Optional[str] = None,
        audio_duration: float = 0,
    ) -> dict:
        """保存一条视频生成记录（按 task_id 去重，存在则更新视频 URL）"""
        now = _now_iso()
        ak_hash = self._ak_hash(access_key)
        video_urls_json = json.dumps(video_urls, ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            try:
                existing = conn.execute(
                    "SELECT id FROM video_records WHERE ak_hash = ? AND task_id = ?",
                    (ak_hash, task_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE video_records SET video_urls = ?, updated_at = ? WHERE id = ?",
                        (video_urls_json, now, existing["id"]),
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT * FROM video_records WHERE id = ?", (existing["id"],)
                    ).fetchone()
                else:
                    cursor = conn.execute(
                        """INSERT INTO video_records
                           (ak_hash, resource_id, task_id, audio_url, audio_duration,
                            mode, video_urls, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ak_hash, resource_id, task_id, audio_url, audio_duration,
                         mode, video_urls_json, now, now),
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT * FROM video_records WHERE id = ?", (cursor.lastrowid,)
                    ).fetchone()
                return self._vr_row_to_dict(row)
            finally:
                conn.close()

    def list_video_records(
        self,
        access_key: str,
        tag: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict]:
        """查询当前 AK 的视频记录列表，支持标签筛选和关键字搜索，按时间倒序"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                conditions = ["ak_hash = ?"]
                params = [ak_hash]

                if tag:
                    # 兼容两种存储格式：直接中文 和 Unicode 转义（如 \u6d4b\u8bd5）
                    escaped_tag = json.dumps(tag, ensure_ascii=True)[1:-1]
                    conditions.append("(tags LIKE ? OR tags LIKE ?)")
                    params.append(f'%"{tag}"%')
                    params.append(f'%"{escaped_tag}"%')

                if keyword:
                    conditions.append(
                        "(resource_id LIKE ? OR task_id LIKE ? OR notes LIKE ?)"
                    )
                    kw = f"%{keyword}%"
                    params.extend([kw, kw, kw])

                where = " AND ".join(conditions)
                rows = conn.execute(
                    f"SELECT * FROM video_records WHERE {where} "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                return [self._vr_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_video_record(self, access_key: str, record_id: int) -> Optional[dict]:
        """获取单条视频记录（AK 隔离）"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM video_records WHERE id = ? AND ak_hash = ?",
                    (record_id, ak_hash),
                ).fetchone()
                return self._vr_row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_video_record_meta(
        self,
        access_key: str,
        record_id: int,
        tags: Optional[list] = None,
        notes: Optional[str] = None,
    ) -> Optional[dict]:
        """更新视频记录的标签和备注"""
        now = _now_iso()
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                # 先获取现有记录
                existing = conn.execute(
                    "SELECT * FROM video_records WHERE id = ? AND ak_hash = ?",
                    (record_id, ak_hash),
                ).fetchone()
                if not existing:
                    return None

                current_tags = existing["tags"]
                current_notes = existing["notes"]

                new_tags = json.dumps(tags, ensure_ascii=False) if tags is not None else current_tags
                new_notes = notes if notes is not None else current_notes

                conn.execute(
                    """UPDATE video_records
                       SET tags = ?, notes = ?, updated_at = ?
                       WHERE id = ? AND ak_hash = ?""",
                    (new_tags, new_notes, now, record_id, ak_hash),
                )
                conn.commit()

                row = conn.execute(
                    "SELECT * FROM video_records WHERE id = ?", (record_id,)
                ).fetchone()
                return self._vr_row_to_dict(row)
            finally:
                conn.close()

    def delete_video_record(self, access_key: str, record_id: int) -> bool:
        """删除一条视频记录（AK 隔离）"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM video_records WHERE id = ? AND ak_hash = ?",
                    (record_id, ak_hash),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_all_tags(self, access_key: str) -> list[str]:
        """获取当前 AK 所有已使用的标签（去重）"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT tags FROM video_records WHERE ak_hash = ?",
                    (ak_hash,),
                ).fetchall()
                tags_set = set()
                for r in rows:
                    if r["tags"]:
                        try:
                            tag_list = json.loads(r["tags"])
                            if isinstance(tag_list, list):
                                tags_set.update(tag_list)
                        except (json.JSONDecodeError, TypeError):
                            pass
                return sorted(tags_set)
            finally:
                conn.close()

    # ─── 任务跟踪 ─────────────────────────────────────────────

    def save_task(
        self,
        access_key: str,
        task_id: str,
        task_type: str,
        mode: str,
        label: str = "",
        notes: str = "",
        resource_id: str = "",
        image_url: str = "",
        audio_url: str = "",
        audio_duration: float = 0,
    ) -> dict:
        """保存一条任务记录"""
        now = _now_iso()
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    """INSERT INTO tasks
                       (ak_hash, task_id, task_type, resource_id, label, notes,
                        status, mode, image_url, audio_url, audio_duration,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?)""",
                    (ak_hash, task_id, task_type, resource_id, label, notes,
                     mode, image_url, audio_url, audio_duration, now, now),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                return self._task_row_to_dict(row)
            finally:
                conn.close()

    def update_task_status(
        self,
        access_key: str,
        task_id: str,
        status: str,
        resource_id: str = "",
        error_message: str = "",
    ) -> Optional[dict]:
        """更新任务状态"""
        now = _now_iso()
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                updates = ["status = ?", "updated_at = ?"]
                params = [status, now]
                if resource_id:
                    updates.append("resource_id = ?")
                    params.append(resource_id)
                if error_message:
                    updates.append("error_message = ?")
                    params.append(error_message)
                params.extend([task_id, ak_hash])

                conn.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ? AND ak_hash = ?",
                    params,
                )
                conn.commit()

                row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ? AND ak_hash = ?",
                    (task_id, ak_hash),
                ).fetchone()
                return self._task_row_to_dict(row) if row else None
            finally:
                conn.close()

    def list_tasks(
        self,
        access_key: str,
        label: Optional[str] = None,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """查询任务列表，支持按标签/状态/类型筛选"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                conditions = ["ak_hash = ?"]
                params = [ak_hash]
                if label:
                    conditions.append("label LIKE ?")
                    params.append(f"%{label}%")
                if status:
                    conditions.append("status = ?")
                    params.append(status)
                if task_type:
                    conditions.append("task_type = ?")
                    params.append(task_type)
                where = " AND ".join(conditions)
                rows = conn.execute(
                    f"SELECT * FROM tasks WHERE {where} "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
                return [self._task_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def get_pending_tasks(self, access_key: str) -> list[dict]:
        """获取所有未完成的任务（用于轮询刷新状态）"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM tasks
                       WHERE ak_hash = ?
                       AND status NOT IN ('done', 'failed', 'not_found', 'expired')
                       ORDER BY created_at DESC""",
                    (ak_hash,),
                ).fetchall()
                return [self._task_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def search_tasks(self, access_key: str, keyword: str, limit: int = 20) -> list[dict]:
        """通过标签或备注关键字搜索任务"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                kw = f"%{keyword}%"
                rows = conn.execute(
                    """SELECT * FROM tasks
                       WHERE ak_hash = ?
                       AND (label LIKE ? OR notes LIKE ? OR task_id LIKE ?)
                       ORDER BY created_at DESC LIMIT ?""",
                    (ak_hash, kw, kw, kw, limit),
                ).fetchall()
                return [self._task_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def delete_task(self, access_key: str, task_id: str) -> bool:
        """删除一条任务记录"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM tasks WHERE task_id = ? AND ak_hash = ?",
                    (task_id, ak_hash),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_task_labels(self, access_key: str) -> list[str]:
        """获取当前 AK 所有已使用的任务标签（去重）"""
        ak_hash = self._ak_hash(access_key)
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT label FROM tasks WHERE ak_hash = ? AND label != '' ORDER BY label",
                    (ak_hash,),
                ).fetchall()
                return [r["label"] for r in rows if r["label"]]
            finally:
                conn.close()

    @staticmethod
    def _task_row_to_dict(row) -> dict:
        if row is None:
            return None
        return dict(row)

    # ─── 视频记录（接上） ─────────────────────────────────────

    @staticmethod
    def _vr_row_to_dict(row) -> dict:
        """将 video_records 行转为字典，反序列化 JSON 字段"""
        if row is None:
            return None
        d = dict(row)
        for field in ("video_urls", "tags"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = [] if field in ("video_urls", "tags") else d[field]
        return d


# 全局单例
_store: Optional[AvatarStore] = None


def get_store() -> AvatarStore:
    global _store
    if _store is None:
        _store = AvatarStore()
    return _store
