"""
用 OnLLMRequestEvent钩子做单点拦截，一旦命中封禁名单，
立即调用 event.stop_event() 终止事件传播

由指令显式触发的 LLM 调用默认放行，
因为这类请求通常属于机器人对指令的“功能性回应”，而非普通聊天。

"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import json_response, request

from .utils.banner import print_banner

LOG_PREFIX = "[影子封禁]"
PLUGIN_NAME = "astrbot_plugin_shadow_ban"

# 面板中保留的最近拦截记录条数。
RECENT_BLOCK_MAX = 100

# 插件 KV 存储中的键名。
BLOCK_COUNT_KEY = "block_count"
RECENT_STORE_KEY = "recent_blocks"

# 名单规模与单条目长度上限，避免通过面板写入异常大的配置。
MAX_BLOCKED_ENTRIES = 2000
MAX_ENTRY_LENGTH = 128

# 最近记录的延迟落盘时间（秒），避免高频拦截时频繁写库。
RECENT_FLUSH_DELAY = 3.0

# 可在面板中切换的布尔开关。
TOGGLE_KEYS: tuple[str, ...] = (
    "enable",
    "enable_group",
    "enable_private",
    "allow_command_llm",
)

# 兼容导入：不同 AstrBot 版本中指令过滤器的模块路径可能不同。
# 导入失败时退化为仅依赖框架写入的事件附加信息进行判定，不影响主干功能。
try:  # pragma: no cover - 仅在核心 API 变动时触发
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.command_group import CommandGroupFilter

    _COMMAND_FILTER_TYPES: tuple[type, ...] = (CommandFilter, CommandGroupFilter)
except Exception:  # noqa: BLE001
    _COMMAND_FILTER_TYPES = ()


class ShadowBanPlugin(Star):
    """影子封禁插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        """AstrBot 会自动解析 ``_conf_schema.json`` 并将配置注入到 ``__init__``。"""
        super().__init__(context)
        self.config = config
        # 最近的拦截记录（环形缓冲，供 WebUI 面板展示）。
        self._recent_blocks: deque[dict] = deque(maxlen=RECENT_BLOCK_MAX)
        # 累计拦截次数（持久化于插件 KV 存储，跨重载 / 重启保留）。
        self._block_count = 0
        # 标记状态是否已从存储加载，避免自增时覆盖历史数据。
        self._state_loaded = False
        self._state_lock = asyncio.Lock()
        # 名单解析结果缓存，消息高频到达时避免重复解析配置。
        self._blocked_cache_key: tuple[str, ...] | None = None
        self._blocked_cache: dict[str, set[str]] = {"*": set()}
        # 运行期统计。
        self._started_at = time.time()
        self._version = self._resolve_version()
        self._last_block_ts: float | None = None
        self._recent_flush_task: asyncio.Task | None = None

        # 启动横幅：打印 Pancakes-Labs ASCII art（bold_cyan 配色）。
        print_banner(self._version)

        # 向 WebUI 注册插件面板的后端 API。
        self._register_web_apis(context)

        # 初始化时打印一次名单规模，便于人工核对配置是否被正确加载。
        blocked = self._parse_blocked_users(self.config.get("blocked_users", []))
        total = sum(len(ids) for ids in blocked.values())
        logger.info(
            f"{LOG_PREFIX} 插件初始化完成，共加载 {total} 条封禁名单。",
        )

    @staticmethod
    def _resolve_version() -> str:
        """从插件目录的 metadata.yaml 读取版本号，读取失败时返回 unknown。"""
        try:
            from pathlib import Path

            meta_path = Path(__file__).resolve().parent / "metadata.yaml"
            for line in meta_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("version:"):
                    value = line.split(":", 1)[1].split("#", 1)[0].strip()
                    return value or "unknown"
        except Exception:  # noqa: BLE001
            pass
        return "unknown"

    def _register_web_apis(self, context: Context) -> None:
        """注册面板所需的后端 Web API。"""
        routes: tuple[tuple[str, Any, list[str], str], ...] = (
            (
                f"/{PLUGIN_NAME}/dashboard/status",
                self.api_status,
                ["GET"],
                "影子封禁运行状态",
            ),
            (
                f"/{PLUGIN_NAME}/dashboard/recent",
                self.api_recent,
                ["GET"],
                "影子封禁最近拦截记录",
            ),
            (
                f"/{PLUGIN_NAME}/dashboard/settings",
                self.api_update_settings,
                ["POST"],
                "更新影子封禁开关",
            ),
            (
                f"/{PLUGIN_NAME}/dashboard/blocked",
                self.api_update_blocked,
                ["POST"],
                "增删影子封禁名单",
            ),
            (
                f"/{PLUGIN_NAME}/dashboard/recent/clear",
                self.api_clear_recent,
                ["POST"],
                "清空影子封禁拦截记录",
            ),
        )
        for route, handler, methods, desc in routes:
            try:
                context.register_web_api(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001
                # 注册失败不应影响核心拦截能力。
                logger.warning(f"{LOG_PREFIX} 注册面板 API {route} 失败：{exc}")

    # ==================================================================
    # 配置解析
    # ==================================================================

    @staticmethod
    def _parse_blocked_users(raw: object) -> dict[str, set[str]]:
        """将配置中的封禁名单解析为 {平台: {用户ID}} 映射。

        规则：

        - 裸 ID 归入通配平台 ``"*"``，在任意平台生效；
        - 带平台前缀的 ID 仅在指定平台生效。
        """
        global_ids: set[str] = set()
        platform_ids: dict[str, set[str]] = {}

        if not isinstance(raw, (list, tuple)):
            return {"*": global_ids}

        for item in raw:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            if ":" in text:
                platform, _, uid = text.partition(":")
                platform = platform.strip()
                uid = uid.strip()
                if platform and uid:
                    platform_ids.setdefault(platform, set()).add(uid)
            else:
                global_ids.add(text)

        result: dict[str, set[str]] = {"*": global_ids}
        result.update(platform_ids)
        return result

    def _blocked_map(self, *, force: bool = False) -> dict[str, set[str]]:
        """返回缓存的名单映射，配置变更时自动重建。"""
        raw = self.config.get("blocked_users", []) or []
        if not isinstance(raw, (list, tuple)):
            raw = []
        cache_key = tuple(str(item).strip() for item in raw if item is not None)
        if force or cache_key != self._blocked_cache_key:
            self._blocked_cache = self._parse_blocked_users(raw)
            self._blocked_cache_key = cache_key
        return self._blocked_cache

    @staticmethod
    def _normalize_entries(values: object) -> list[str]:
        """规整名单条目：去空白、去重、过滤超长与空值。"""
        if values is None:
            return []
        if isinstance(values, str):
            candidates: list[Any] = [values]
        elif isinstance(values, (list, tuple, set)):
            candidates = list(values)
        else:
            candidates = [values]

        result: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item is None:
                continue
            text = " ".join(str(item).split())
            if not text or len(text) > MAX_ENTRY_LENGTH:
                continue
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    # ==================================================================
    # 判定逻辑
    # ==================================================================

    def _is_blocked_user(self, event: AstrMessageEvent) -> bool:
        """判断当前消息发送者是否命中封禁名单。"""
        sender_id = str(event.get_sender_id() or "").strip()
        if not sender_id:
            return False

        # 配置变更时缓存会自动失效，WebUI 保存后即时生效。
        blocked = self._blocked_map()

        if sender_id in blocked.get("*", set()):
            return True

        platform = str(event.get_platform_name() or "").strip()
        return bool(platform and sender_id in blocked.get(platform, set()))

    @staticmethod
    def _is_command_invoked(event: AstrMessageEvent) -> bool:
        """判断本次 LLM 请求是否由指令（CommandFilter / CommandGroupFilter）触发。"""
        # 框架在指令过滤器通过时，会把解析结果写入 handlers_parsed_params。
        try:
            if event.get_extra("handlers_parsed_params"):
                return True
        except Exception:  # noqa: BLE001
            pass

        # 兜底方案：直接检查被激活的 handler 是否带有指令过滤器。
        if not _COMMAND_FILTER_TYPES:
            return False
        try:
            activated = event.get_extra("activated_handlers") or []
        except Exception:  # noqa: BLE001
            return False

        for handler in activated:
            for flt in getattr(handler, "event_filters", []) or []:
                if isinstance(flt, _COMMAND_FILTER_TYPES):
                    return True
        return False

    def _should_intercept(self, event: AstrMessageEvent) -> bool:
        """综合配置判定是否应当拦截本次 LLM 请求。"""
        if not self.config.get("enable", True):
            return False

        if not self._is_blocked_user(event):
            return False

        # 群聊 / 私聊独立开关。
        if event.is_private_chat():
            if not self.config.get("enable_private", False):
                return False
        elif not self.config.get("enable_group", True):
            return False

        # 指令触发的 LLM 调用放行（非 LLM 响应的功能性回复）。
        if self.config.get("allow_command_llm", True) and self._is_command_invoked(
            event,
        ):
            return False

        return True

    @staticmethod
    def _preview(text: str, limit: int = 30) -> str:
        """截取消息预览，避免日志过长。"""
        text = (text or "").replace("\n", " ").strip()
        return text[:limit] + ("..." if len(text) > limit else "")

    # ==================================================================
    # 持久化
    # ==================================================================

    async def _ensure_state_loaded(self) -> None:
        """首次访问时从插件 KV 存储恢复累计次数与最近记录。

        构造函数无法 await，故延迟加载；若不先加载就直接自增 / 覆盖，
        会把历史数据丢失。
        """
        if self._state_loaded:
            return
        async with self._state_lock:
            if self._state_loaded:
                return

            try:
                stored = await self.get_kv_data(BLOCK_COUNT_KEY, 0)
                self._block_count = int(stored) if stored else 0
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"{LOG_PREFIX} 读取累计拦截次数失败，将从 0 开始：{exc}",
                )
                self._block_count = 0

            try:
                stored_recent = await self.get_kv_data(RECENT_STORE_KEY, [])
                if isinstance(stored_recent, list):
                    self._recent_blocks.clear()
                    for item in stored_recent[-RECENT_BLOCK_MAX:]:
                        if isinstance(item, dict):
                            self._recent_blocks.append(item)
                    if self._recent_blocks:
                        last_ts = self._recent_blocks[-1].get("ts")
                        if isinstance(last_ts, (int, float)):
                            self._last_block_ts = float(last_ts)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{LOG_PREFIX} 读取最近拦截记录失败：{exc}")

            self._state_loaded = True

    async def _persist_block_count(self) -> None:
        """将累计拦截次数写入插件 KV 存储。"""
        try:
            await self.put_kv_data(BLOCK_COUNT_KEY, int(self._block_count))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 持久化累计拦截次数失败：{exc}")

    async def _persist_recent(self) -> None:
        """将最近拦截记录写入插件 KV 存储。"""
        try:
            await self.put_kv_data(RECENT_STORE_KEY, list(self._recent_blocks))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 持久化最近拦截记录失败：{exc}")

    def _schedule_recent_flush(self) -> None:
        """延迟合并写入最近记录，降低高频拦截时的写库压力。"""
        if self._recent_flush_task and not self._recent_flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _flush() -> None:
            try:
                await asyncio.sleep(RECENT_FLUSH_DELAY)
                await self._persist_recent()
            except asyncio.CancelledError:  # pragma: no cover - 卸载时取消
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{LOG_PREFIX} 延迟落盘最近记录失败：{exc}")

        self._recent_flush_task = loop.create_task(_flush())

    async def _save_config(self, updates: dict[str, Any] | None = None) -> bool:
        """更新并持久化插件配置，兼容不同 AstrBot 版本。"""
        if updates:
            try:
                self.config.update(updates)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{LOG_PREFIX} 更新配置失败：{exc}")
                return False

        # 配置可能被外部改写，这里主动让名单缓存失效。
        self._blocked_cache_key = None

        save_async = getattr(self.config, "save_config_async", None)
        try:
            if callable(save_async):
                result = await save_async()
                return True if result is None else bool(result)
            save_sync = getattr(self.config, "save_config", None)
            if callable(save_sync):
                await asyncio.to_thread(save_sync)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 保存配置失败：{exc}")
            return False

        logger.warning(
            f"{LOG_PREFIX} 当前 AstrBot 版本不支持自动保存配置，请在 WebUI 中手动保存。",
        )
        return False

    async def _record_block(self, event: AstrMessageEvent) -> None:
        """记录一次拦截事件，供面板展示并持久化。"""
        await self._ensure_state_loaded()
        self._block_count += 1
        now = time.time()
        self._last_block_ts = now
        is_private = event.is_private_chat()
        self._recent_blocks.append(
            {
                "ts": now,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "scope": "私聊" if is_private else "群聊",
                "scope_key": "private" if is_private else "group",
                "platform": event.get_platform_name() or "",
                "user_id": str(event.get_sender_id() or ""),
                "group_id": str(event.get_group_id() or ""),
                "content": self._preview(event.message_str, 60),
            },
        )
        await self._persist_block_count()
        self._schedule_recent_flush()

    # ==================================================================
    # LLM 请求钩子（单点拦截）
    # ==================================================================

    @filter.on_llm_request(priority=10000)
    async def on_llm_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在 LLM 请求发出前进行拦截。"""
        if event.is_stopped():
            return
        try:
            if not self._should_intercept(event):
                return
        except Exception as exc:  # noqa: BLE001
            # 判定异常时选择放行，避免误伤正常用户。
            logger.error(f"{LOG_PREFIX} 判定封禁状态时出错，已放行：{exc}")
            return

        # 终止事件传播：管线将跳过后续所有阶段，LLM 不会被调用，也不会回送消息。
        event.stop_event()

        scope = "私聊" if event.is_private_chat() else "群聊"
        logger.info(
            f"{LOG_PREFIX} 已拦截来自{scope}的 LLM 请求 | "
            f"平台为{event.get_platform_name()} "
            f"用户是 {event.get_sender_id()} "
            f"群组：{event.get_group_id() or '-'} "
            f"内容：{self._preview(event.message_str)}",
        )
        await self._record_block(event)

    # ==================================================================
    # 面板后端 API
    # ==================================================================

    def _settings_snapshot(self) -> dict[str, bool]:
        """返回当前开关状态快照。"""
        return {
            "enable": bool(self.config.get("enable", True)),
            "enable_group": bool(self.config.get("enable_group", True)),
            "enable_private": bool(self.config.get("enable_private", False)),
            "allow_command_llm": bool(self.config.get("allow_command_llm", True)),
        }

    async def api_status(self):
        """返回插件运行状态与封禁名单概况。"""
        await self._ensure_state_loaded()
        raw: object = self.config.get("blocked_users", []) or []
        if not isinstance(raw, (list, tuple)):
            raw = []
        parsed = self._blocked_map(force=True)
        global_ids = sorted(parsed.get("*", set()))
        platform_ids = {
            platform: sorted(ids) for platform, ids in parsed.items() if platform != "*"
        }
        unique_ids: set[str] = set(global_ids)
        for ids in platform_ids.values():
            unique_ids.update(ids)

        return json_response(
            {
                "plugin_version": self._version,
                **self._settings_snapshot(),
                "blocked_users": self._normalize_entries(raw),
                "global_ids": global_ids,
                "platform_ids": platform_ids,
                "total_blocked_users": len(unique_ids),
                "entry_count": sum(len(ids) for ids in parsed.values()),
                "global_count": len(global_ids),
                "platform_count": sum(len(ids) for ids in platform_ids.values()),
                "block_count": self._block_count,
                "recent_count": len(self._recent_blocks),
                "recent_capacity": RECENT_BLOCK_MAX,
                "last_block_ts": self._last_block_ts,
                "uptime_seconds": max(0, int(time.time() - self._started_at)),
            },
        )

    async def api_recent(self):
        """返回最近的拦截记录。"""
        await self._ensure_state_loaded()
        try:
            limit = request.query.get("limit", 30, type=int) or 30
        except Exception:  # noqa: BLE001
            limit = 30
        limit = max(1, min(int(limit), RECENT_BLOCK_MAX))
        # 最新的记录排在最前。
        items = list(self._recent_blocks)[-limit:][::-1]
        return json_response(
            {
                "items": items,
                "count": len(self._recent_blocks),
                "capacity": RECENT_BLOCK_MAX,
                "limit": limit,
            },
        )

    async def api_update_settings(self):
        """更新面板开关，并立刻持久化到插件配置。"""
        body = await request.json({})
        if not isinstance(body, dict):
            return json_response({"ok": False, "message": "无效的请求体"})

        updates: dict[str, bool] = {}
        for key in TOGGLE_KEYS:
            if key in body:
                updates[key] = bool(body[key])

        if not updates:
            return json_response(
                {"ok": False, "message": "没有需要更新的配置项"},
            )

        saved = await self._save_config(updates)
        if saved:
            logger.info(f"{LOG_PREFIX} 面板更新开关：{updates}")
        return json_response(
            {
                "ok": True,
                "saved": saved,
                "applied": updates,
                "settings": self._settings_snapshot(),
            },
        )

    async def api_update_blocked(self):
        """增删 / 覆盖面板中的封禁名单。"""
        body = await request.json({})
        if not isinstance(body, dict):
            return json_response({"ok": False, "message": "无效的请求体"})

        action = str(body.get("action") or "add").strip().lower()
        current = self._normalize_entries(self.config.get("blocked_users", []) or [])

        if action == "add":
            payload = body.get("values", body.get("value"))
            entries = self._normalize_entries(payload)
            if not entries:
                return json_response(
                    {"ok": False, "message": "封禁条目不能为空"},
                )
            merged = self._normalize_entries([*current, *entries])
            added = [entry for entry in merged if entry not in current]
            if not added:
                return json_response(
                    {
                        "ok": True,
                        "saved": True,
                        "unchanged": True,
                        "added": [],
                        "entries": current,
                        "message": "条目已存在",
                    },
                )
            if len(merged) > MAX_BLOCKED_ENTRIES:
                return json_response(
                    {
                        "ok": False,
                        "message": f"封禁名单不能超过 {MAX_BLOCKED_ENTRIES} 条",
                    },
                )
            saved = await self._save_config({"blocked_users": merged})
            logger.info(f"{LOG_PREFIX} 面板新增封禁条目：{added}")
            return json_response(
                {
                    "ok": True,
                    "saved": saved,
                    "unchanged": False,
                    "added": added,
                    "entries": merged,
                },
            )

        if action == "remove":
            payload = body.get("values", body.get("value"))
            entries = self._normalize_entries(payload)
            if not entries:
                return json_response(
                    {"ok": False, "message": "需要指定要移除的条目"},
                )
            removals = set(entries)
            merged = [entry for entry in current if entry not in removals]
            removed = [entry for entry in current if entry in removals]
            if not removed:
                return json_response(
                    {
                        "ok": True,
                        "saved": True,
                        "unchanged": True,
                        "removed": [],
                        "entries": current,
                        "message": "条目不存在",
                    },
                )
            saved = await self._save_config({"blocked_users": merged})
            logger.info(f"{LOG_PREFIX} 面板移除封禁条目：{removed}")
            return json_response(
                {
                    "ok": True,
                    "saved": saved,
                    "unchanged": False,
                    "removed": removed,
                    "entries": merged,
                },
            )

        if action == "replace":
            entries = self._normalize_entries(body.get("values"))
            if len(entries) > MAX_BLOCKED_ENTRIES:
                return json_response(
                    {
                        "ok": False,
                        "message": f"封禁名单不能超过 {MAX_BLOCKED_ENTRIES} 条",
                    },
                )
            saved = await self._save_config({"blocked_users": entries})
            logger.info(f"{LOG_PREFIX} 面板覆盖封禁名单，共 {len(entries)} 条。")
            return json_response(
                {
                    "ok": True,
                    "saved": saved,
                    "unchanged": False,
                    "entries": entries,
                },
            )

        return json_response({"ok": False, "message": f"未知操作：{action}"})

    async def api_clear_recent(self):
        """清空最近拦截记录，可选同时重置累计次数。"""
        await self._ensure_state_loaded()
        body = await request.json({})
        reset_count = bool(body.get("reset_count")) if isinstance(body, dict) else False

        removed = len(self._recent_blocks)
        self._recent_blocks.clear()
        self._last_block_ts = None

        if reset_count:
            self._block_count = 0
            await self._persist_block_count()

        await self._persist_recent()
        logger.info(
            f"{LOG_PREFIX} 面板清空拦截记录，共移除 {removed} 条"
            f"{'，并重置累计次数' if reset_count else ''}。",
        )
        return json_response(
            {
                "ok": True,
                "removed": removed,
                "reset_count": reset_count,
                "block_count": self._block_count,
            },
        )

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def terminate(self) -> None:
        """插件被卸载 / 停用时调用。"""
        if self._recent_flush_task and not self._recent_flush_task.done():
            self._recent_flush_task.cancel()

        # 兜底落盘；未加载过时不自增，避免覆盖历史值。
        if self._state_loaded:
            await self._persist_block_count()
            await self._persist_recent()

        logger.info(f"{LOG_PREFIX} 插件已卸载，累计拦截 {self._block_count} 次。")
