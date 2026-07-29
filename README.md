# astrbot_plugin_wecom_handoff

AstrBot 微信客服转人工插件。

插件会监听企业微信「微信客服」私聊消息：当用户消息命中关键词时，调用企业微信 `kf/service_state/trans` 将会话切换到人工接待，并阻断后续 LLM / RAG 回复。插件也保留了 LLM 调用前兜底拦截，避免漏网。

## 前提

- AstrBot 已启用 `wecom` 平台适配器，并配置了微信客服 `kf_name`。
- 企业微信后台已把自建应用配置到「微信客服 - 可调用接口的应用」。
- 被分配的接待人员已在企业微信激活使用，并处于「正在接待」。
- 该客服账号已允许通过 API 管理。

企业微信官方文档里要求：转到 `service_state=3` 时需要填写 `servicer_userid`，否则会失败。

## 配置

插件目录包含 `_conf_schema.json`，AstrBot 加载后会生成插件配置。

核心配置项：

- `trigger_keywords`: 触发词，默认 `["人工"]`。
- `match_mode`: 默认 `exact`，只有完整消息等于触发词才触发；可改为 `contains`。
- `servicer_userid`: 必填。要接待该会话的企业微信成员 userid；第三方应用填密文 userid/open_userid。
- `success_message`: 转人工成功后，通过 `kf/send_msg_on_event` 发送的提示。
- `failure_message`: 转人工失败或缺少配置时发送给用户的提示。
- `remember_handoff_users`: 开启后，插件运行期间已转人工用户的后续消息会继续阻断 LLM。
- `verify_handoff_state`: 默认开启。已转人工用户再次发消息时，调用 `kf/service_state/get` 查询当前会话；人工客服结束会话后，插件会清除本地标记并恢复 AI。查询失败时保持拦截，避免 AI 在人工会话中抢答。
- `send_processing_message`: 默认关闭。开启后，普通微信客服消息进入模型处理前会先发送一条处理中提示。
- `processing_message`: 处理中提示内容，默认“正在为您查询，请稍候。”；留空则不发送。

## 行为

默认只在用户完整发送：

```text
人工
```

时触发转人工。

触发后插件会：

1. 立即 `event.stop_event()`，阻断后续 LLM。
2. 调用 `kf/service_state/trans`，目标状态固定为 `service_state=3`。
3. 如果企业微信返回 `msg_code`，用 `kf/send_msg_on_event` 发送成功提示。
4. 将该 `external_userid` 记入内存集合，避免继续进入 LLM；后续收到该用户消息时查询会话状态，确认人工会话已结束后自动恢复 AI。

## 处理中提示

微信客服不支持流式输出时，可将 `send_processing_message` 设为 `true`。插件会在普通微信客服消息进入模型处理前先发送 `processing_message`，然后由 AstrBot 按原有流程发送完整模型回复。转人工触发词与人工接待中的消息不会发送该提示。

## 常见问题

`servicer_userid` 为空：
插件不会调用转人工接口，会直接发送失败提示。

企业微信返回 `95014`：
通常是接待人员未激活企业微信、未处于正在接待、或不在应用可见范围内。

用户说了“人工智能”也触发：
把 `match_mode` 保持为 `exact`；需要“转人工”“人工客服”等短语时，把这些短语加入 `trigger_keywords`。

转人工成功但没看到成功提示：
检查企业微信是否返回了 `msg_code`，以及插件日志里 `kf/send_msg_on_event` 是否报错。
