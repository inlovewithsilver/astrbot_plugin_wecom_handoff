from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register


DEFAULT_TRIGGER_KEYWORDS = ["人工"]
DEFAULT_SUCCESS_MESSAGE = "正在为您转接人工客服，请稍候。"
DEFAULT_FAILURE_MESSAGE = "当前人工客服暂时无法接入，请稍后再试。"
DEFAULT_PROCESSING_MESSAGE = "正在为您查询，请稍候。"


@register(
    "astrbot_plugin_wecom_handoff",
    "Codex",
    "微信客服关键词转人工插件",
    "1.0.0",
)
class WecomHandoffPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config or {}
        self.handoff_users: set[str] = set()

        self.enabled = bool(self.config.get("enabled", True))
        self.trigger_keywords = self._normalize_keywords(
            self.config.get("trigger_keywords", DEFAULT_TRIGGER_KEYWORDS),
        )
        self.match_mode = str(self.config.get("match_mode", "exact")).strip().lower()
        if self.match_mode not in {"exact", "contains"}:
            logger.warning("wecom_handoff: unknown match_mode=%s, fallback to exact", self.match_mode)
            self.match_mode = "exact"

        self.servicer_userid = str(self.config.get("servicer_userid", "")).strip()
        self.success_message = str(
            self.config.get("success_message", DEFAULT_SUCCESS_MESSAGE),
        )
        self.failure_message = str(
            self.config.get("failure_message", DEFAULT_FAILURE_MESSAGE),
        )
        self.remember_handoff_users = bool(
            self.config.get("remember_handoff_users", True),
        )
        self.send_event_response_message = bool(
            self.config.get("send_event_response_message", True),
        )
        self.verify_handoff_state = bool(
            self.config.get("verify_handoff_state", True),
        )
        self.send_processing_message = bool(
            self.config.get("send_processing_message", False),
        )
        self.processing_message = str(
            self.config.get("processing_message", DEFAULT_PROCESSING_MESSAGE),
        ).strip()
        self.api_timeout = int(self.config.get("api_timeout", 10) or 10)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=100)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """监听微信客服私聊消息，命中关键词时转人工。"""
        handled = await self._handle_handoff(event)
        if not handled and self.send_processing_message and self.processing_message:
            await self._send_processing_message(event)

    @filter.on_llm_request(priority=100)
    async def on_llm_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在 LLM 调用前兜底拦截转人工关键词。"""
        del req
        await self._handle_handoff(event)

    async def _handle_handoff(self, event: AstrMessageEvent) -> bool:
        if not self.enabled or not self._is_wechat_kf_event(event):
            return False

        external_userid = event.get_sender_id()
        open_kfid = event.get_self_id()
        if self.remember_handoff_users and external_userid in self.handoff_users:
            if self.verify_handoff_state:
                is_human_serving = await self._is_human_serving(
                    event=event,
                    open_kfid=open_kfid,
                    external_userid=external_userid,
                )
                if is_human_serving is False:
                    self.handoff_users.discard(external_userid)
                    logger.info(
                        "wecom_handoff: restored AI for user %s after customer service ended",
                        external_userid,
                    )
                else:
                    event.stop_event()
                    logger.info(
                        "wecom_handoff: blocked LLM for handed-off user %s",
                        external_userid,
                    )
                    return True
            else:
                event.stop_event()
                logger.info("wecom_handoff: blocked LLM for handed-off user %s", external_userid)
                return True

        message = (event.get_message_str() or "").strip()
        if not message or not self._matches_trigger(message):
            return False

        event.stop_event()

        if not open_kfid or not external_userid:
            logger.warning(
                "wecom_handoff: missing open_kfid or external_userid, open_kfid=%s external_userid=%s",
                open_kfid,
                external_userid,
            )
            await self._send_failure(event)
            return True

        if not self.servicer_userid:
            logger.warning(
                "wecom_handoff: servicer_userid is required when transferring to service_state=3",
            )
            await self._send_failure(event)
            return True

        try:
            result = await self._transfer_to_servicer(
                event=event,
                open_kfid=open_kfid,
                external_userid=external_userid,
            )
        except Exception as exc:
            logger.warning("wecom_handoff: transfer failed: %s", self._format_error(exc))
            await self._send_failure(event)
            return True

        if not self._is_api_success(result):
            logger.warning(
                "wecom_handoff: transfer rejected: %s",
                self._format_api_error(result),
            )
            await self._send_failure(event)
            return True

        if self.remember_handoff_users:
            self.handoff_users.add(external_userid)

        msg_code = result.get("msg_code") if isinstance(result, dict) else None
        if self.success_message and self.send_event_response_message and msg_code:
            try:
                await self._send_event_response_message(event, msg_code, self.success_message)
            except Exception as exc:
                logger.warning(
                    "wecom_handoff: transferred but failed to send event response message: %s",
                    self._format_error(exc),
                )
        elif self.success_message and self.send_event_response_message:
            logger.info(
                "wecom_handoff: transferred user %s but trans response has no msg_code",
                external_userid,
            )

        logger.info(
            "wecom_handoff: transferred external_userid=%s open_kfid=%s servicer_userid=%s",
            external_userid,
            open_kfid,
            self.servicer_userid,
        )
        return True

    @staticmethod
    def _normalize_keywords(value: Any) -> list[str]:
        if not isinstance(value, list):
            value = DEFAULT_TRIGGER_KEYWORDS
        keywords = []
        for item in value:
            keyword = str(item).strip()
            if keyword:
                keywords.append(keyword)
        return keywords or DEFAULT_TRIGGER_KEYWORDS

    def _matches_trigger(self, message: str) -> bool:
        if self.match_mode == "contains":
            return any(keyword in message for keyword in self.trigger_keywords)
        return message in self.trigger_keywords

    @staticmethod
    def _is_wechat_kf_event(event: AstrMessageEvent) -> bool:
        if event.get_platform_name() != "wecom":
            return False

        raw_message = getattr(event.message_obj, "raw_message", None)
        return isinstance(raw_message, dict) and "_wechat_kf_flag" in raw_message

    async def _transfer_to_servicer(
        self,
        *,
        event: AstrMessageEvent,
        open_kfid: str,
        external_userid: str,
    ) -> dict[str, Any]:
        client = getattr(event, "client", None)
        if client is None or not hasattr(client, "post"):
            raise RuntimeError("current event has no WeCom client")

        payload = {
            "open_kfid": open_kfid,
            "external_userid": external_userid,
            "service_state": 3,
            "servicer_userid": self.servicer_userid,
        }
        return await asyncio.to_thread(
            client.post,
            "kf/service_state/trans",
            data=payload,
            timeout=self.api_timeout,
        )

    async def _is_human_serving(
        self,
        *,
        event: AstrMessageEvent,
        open_kfid: str,
        external_userid: str,
    ) -> bool | None:
        """Return None on an unknown state so a human session is never released by error."""
        if not open_kfid or not external_userid:
            logger.warning("wecom_handoff: cannot verify handoff state without identifiers")
            return None

        client = getattr(event, "client", None)
        if client is None or not hasattr(client, "post"):
            logger.warning("wecom_handoff: cannot verify handoff state without WeCom client")
            return None

        payload = {"open_kfid": open_kfid, "external_userid": external_userid}
        try:
            result = await asyncio.to_thread(
                client.post,
                "kf/service_state/get",
                data=payload,
                timeout=self.api_timeout,
            )
        except Exception as exc:
            logger.warning("wecom_handoff: state verification failed: %s", self._format_error(exc))
            return None

        if not self._is_api_success(result):
            logger.warning(
                "wecom_handoff: state verification rejected: %s",
                self._format_api_error(result),
            )
            return None

        return result.get("service_state") == 3

    async def _send_event_response_message(
        self,
        event: AstrMessageEvent,
        msg_code: str,
        text: str,
    ) -> dict[str, Any]:
        client = getattr(event, "client", None)
        if client is None or not hasattr(client, "post"):
            raise RuntimeError("current event has no WeCom client")

        payload = {
            "code": msg_code,
            "msgtype": "text",
            "text": {"content": text},
        }
        return await asyncio.to_thread(
            client.post,
            "kf/send_msg_on_event",
            data=payload,
            timeout=self.api_timeout,
        )

    async def _send_failure(self, event: AstrMessageEvent) -> None:
        if self.failure_message:
            await event.send(MessageChain([Plain(self.failure_message)]))

    async def _send_processing_message(self, event: AstrMessageEvent) -> None:
        client = getattr(event, "client", None)
        kf_message_api = getattr(client, "kf_message", None)
        external_userid = event.get_sender_id()
        open_kfid = event.get_self_id()
        if kf_message_api is None or not external_userid or not open_kfid:
            logger.warning("wecom_handoff: cannot send processing message without WeCom KF client")
            return

        try:
            await asyncio.to_thread(
                kf_message_api.send_text,
                external_userid,
                open_kfid,
                self.processing_message,
            )
        except Exception as exc:
            logger.warning(
                "wecom_handoff: failed to send processing message: %s",
                self._format_error(exc),
            )

    @staticmethod
    def _format_error(exc: Exception) -> str:
        errcode = getattr(exc, "errcode", None)
        errmsg = getattr(exc, "errmsg", None)
        if errcode is not None or errmsg is not None:
            return f"errcode={errcode}, errmsg={errmsg}"
        return str(exc)

    @staticmethod
    def _is_api_success(result: Any) -> bool:
        return isinstance(result, dict) and result.get("errcode", 0) == 0

    @staticmethod
    def _format_api_error(result: Any) -> str:
        if not isinstance(result, dict):
            return f"unexpected response: {result!r}"
        return f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"

    async def terminate(self):
        self.handoff_users.clear()
