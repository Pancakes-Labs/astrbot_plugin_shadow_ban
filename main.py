"""
用 OnLLMRequestEvent钩子做单点拦截，一旦命中封禁名单，
立即调用 event.stop_event() 终止事件传播

由指令显式触发的 LLM 调用默认放行，
因为这类请求通常属于机器人对指令的“功能性回应”，而非普通聊天。

"""

from __future__ import annotations

import time
from collections import deque

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import json_response, request

LOG_PREFIX = "[影子封禁]"
PLUGIN_NAME = "astrbot_plugin_shadow_ban"

# 面板中保留的最近拦截记录条数。
RECENT_BLOCK_MAX = 100

# 累计拦截次数在插件 KV 存储中的键名。
BLOCK_COUNT_KEY = "block_count"

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
        # 最近的拦截记录（内存环形缓冲，供 WebUI 面板展示）。
        self._recent_blocks: deque[dict] = deque(maxlen=RECENT_BLOCK_MAX)
        # 累计拦截次数（持久化于插件 KV 存储，跨重载 / 重启保留）。
        self._block_count = 0
        # 标记累计值是否已从存储加载，避免自增时覆盖历史数据。
        self._block_count_loaded = False

        # 向 WebUI 注册插件面板的后端 API。
        self._register_web_apis(context)

        # 初始化时打印一次名单规模，便于人工核对配置是否被正确加载。
        blocked = self._parse_blocked_users(self.config.get("blocked_users", []))
        total = sum(len(ids) for ids in blocked.values())
        logger.info(
            f"{LOG_PREFIX} 插件初始化完成，共加载 {total} 条封禁名单。",
        )

    def _register_web_apis(self, context: Context) -> None:
        """注册面板所需的后端 Web API。"""
        try:
            context.register_web_api(
                f"/{PLUGIN_NAME}/dashboard/status",
                self.api_status,
                ["GET"],
                "影子封禁运行状态",
            )
            context.register_web_api(
                f"/{PLUGIN_NAME}/dashboard/recent",
                self.api_recent,
                ["GET"],
                "影子封禁最近拦截记录",
            )
        except Exception as exc:  # noqa: BLE001
            # 注册失败不应影响核心拦截能力。
            logger.warning(f"{LOG_PREFIX} 注册面板 API 失败：{exc}")

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

    # ==================================================================
    # 判定逻辑
    # ==================================================================

    def _is_blocked_user(self, event: AstrMessageEvent) -> bool:
        """判断当前消息发送者是否命中封禁名单。"""
        sender_id = str(event.get_sender_id() or "").strip()
        if not sender_id:
            return False

        # 每次判定都重新读取配置，确保 WebUI 保存后即时生效。
        blocked = self._parse_blocked_users(self.config.get("blocked_users", []))

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

    async def _ensure_block_count_loaded(self) -> None:
        """首次访问时从插件 KV 存储读取累计拦截次数。

        构造函数无法 await，故延迟加载；若不先加载就自增，
        会把历史累计值覆盖成本次运行的值。
        """
        if self._block_count_loaded:
            return
        try:
            stored = await self.get_kv_data(BLOCK_COUNT_KEY, 0)
            self._block_count = int(stored) if stored else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 读取累计拦截次数失败，将从 0 开始：{exc}")
            self._block_count = 0
        self._block_count_loaded = True

    async def _persist_block_count(self) -> None:
        """将累计拦截次数写入插件 KV 存储。"""
        try:
            await self.put_kv_data(BLOCK_COUNT_KEY, int(self._block_count))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{LOG_PREFIX} 持久化累计拦截次数失败：{exc}")

    async def _record_block(self, event: AstrMessageEvent) -> None:
        """记录一次拦截事件，累计值持久化，供面板展示。"""
        await self._ensure_block_count_loaded()
        self._block_count += 1
        self._recent_blocks.append(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "scope": "私聊" if event.is_private_chat() else "群聊",
                "platform": event.get_platform_name() or "",
                "user_id": str(event.get_sender_id() or ""),
                "group_id": str(event.get_group_id() or ""),
                "content": self._preview(event.message_str, 60),
            },
        )
        await self._persist_block_count()

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

    async def api_status(self):
        """返回插件运行状态与封禁名单概况。"""
        await self._ensure_block_count_loaded()
        raw = self.config.get("blocked_users", []) or []
        parsed = self._parse_blocked_users(raw)
        global_ids = sorted(parsed.get("*", set()))
        platform_ids = {
            platform: sorted(ids) for platform, ids in parsed.items() if platform != "*"
        }
        return json_response(
            {
                "enable": bool(self.config.get("enable", True)),
                "enable_group": bool(self.config.get("enable_group", True)),
                "enable_private": bool(self.config.get("enable_private", False)),
                "allow_command_llm": bool(
                    self.config.get("allow_command_llm", True),
                ),
                "blocked_users": [str(item) for item in raw if str(item).strip()],
                "global_ids": global_ids,
                "platform_ids": platform_ids,
                "total_blocked_users": sum(len(ids) for ids in parsed.values()),
                "block_count": self._block_count,
            },
        )

    async def api_recent(self):
        """返回最近的拦截记录。"""
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
            },
        )

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def terminate(self) -> None:
        """插件被卸载 / 停用时调用。"""
        # 兜底落盘；未加载过时不自增，避免覆盖历史值。
        if self._block_count_loaded:
            await self._persist_block_count()
        logger.info(f"{LOG_PREFIX} 插件已卸载，累计拦截 {self._block_count} 次。")
