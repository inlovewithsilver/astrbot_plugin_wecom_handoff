# 微信客服转人工

用于 AstrBot 企业微信「微信客服」场景的转人工插件。当用户发送指定关键词时，插件将会话转交给企业微信人工客服，并停止 AI 继续回复；人工客服结束会话后，AI 可自动恢复接待。

## 功能

- 关键词触发转人工，默认仅匹配完整消息 `人工`。
- 调用企业微信 `kf/service_state/trans`，将会话转至指定接待人员。
- 未指定接待人员时，将会话转入待接入池，供多名人工客服接入。
- 转人工后阻断 LLM、知识库和后续 AI 回复，避免人工与 AI 同时回复。
- 用户再次发言时自动查询会话状态；人工客服结束会话后恢复 AI 接待。
- 可选“正在为您查询，请稍候。”等处理中提示，改善长回答等待期间的体验。
- 支持精确匹配与包含匹配，可自定义触发词与提示文案。

## 适用范围

本插件只支持 AstrBot 的 `wecom` 平台适配器中的微信客服模式，不适用于普通企业微信自建应用、群聊或其他消息平台。

## 前置条件

使用前，请确认以下配置均已完成：

1. AstrBot 已启用企业微信 `wecom` 平台，并已配置微信客服账号 `kf_name`。
2. 企业微信后台已将该自建应用设为「微信客服 - 可调用接口的应用」。
3. 微信客服账号已开启 API 管理权限。
4. 用于接待的企业成员已激活企业微信，处于「正在接待」状态，并在应用可见范围内。
5. 如需指定某一位成员接待，已取得该成员的 `userid`。第三方应用场景请使用对应的密文 `userid` / `open_userid`。

企业微信在切换至指定人工接待状态（`service_state=3`）时要求提供 `servicer_userid`。不填写时，插件会切换到待接入池（`service_state=2`）。相关接口说明见[企业微信微信客服文档](https://developer.work.weixin.qq.com/document/path/94669)。

## 安装

发布到 AstrBot 插件市场后，可在 AstrBot WebUI 的插件管理页面搜索“微信客服转人工”并安装。

本地开发或手动安装时，将插件目录放到 AstrBot 的 `data/plugins/` 下，然后在 WebUI 的插件管理页面重载插件。

## 配置

安装后，在 AstrBot 插件配置中填写以下项目。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用插件。 |
| `trigger_keywords` | `["人工"]` | 触发转人工的关键词列表。 |
| `match_mode` | `exact` | `exact` 为完整匹配，`contains` 为包含匹配。 |
| `servicer_userid` | 空 | 可选。填写时指定该成员接待；留空时转入待接入池。 |
| `success_message` | 正在为您转接人工客服，请稍候。 | 转人工成功后的提示。留空则不发送。 |
| `failure_message` | 当前人工客服暂时无法接入，请稍后再试。 | 转人工失败后的提示。留空则不发送。 |
| `remember_handoff_users` | `true` | 是否在插件运行期间记录已转人工的用户。 |
| `verify_handoff_state` | `true` | 已转人工用户再次发言时，查询客服会话状态以恢复 AI。建议保持开启。 |
| `send_processing_message` | `false` | 是否在普通问题进入模型处理前发送处理中提示。 |
| `processing_message` | 正在为您查询，请稍候。 | 处理中提示内容。留空则不发送。 |
| `api_timeout` | `10` | 企业微信 API 调用超时，单位为秒。 |

### 多名成员接待

推荐将多名技服成员配置为同一微信客服账号的接待人员，并将 `servicer_userid` 留空。用户触发转人工后，会话会进入企业微信待接入池，成员可在企业微信客户端接入会话。

插件不会轮询或随机分配成员。这避免将会话分给离线、未处于“正在接待”或已满负荷的成员；实际接待由企业微信客服工作台管理。

### 指定人员配置

```json
{
  "trigger_keywords": ["人工", "转人工", "人工客服"],
  "match_mode": "exact",
  "servicer_userid": "zhangsan",
  "verify_handoff_state": true,
  "send_processing_message": true,
  "processing_message": "正在为您查询，请稍候。"
}
```

`match_mode` 为 `exact` 时，只有消息与关键词完全相同才会触发。例如用户发送“人工智能”不会转人工。需要识别更自然的表达时，可将常用短语加入 `trigger_keywords`；不建议直接使用 `contains`，以免误触发。

### 待接入池配置

```json
{
  "trigger_keywords": ["人工", "转人工", "人工客服"],
  "match_mode": "exact",
  "servicer_userid": "",
  "verify_handoff_state": true
}
```

## 工作流程

### 普通 AI 对话

1. 用户发送消息。
2. 若开启 `send_processing_message`，插件立即发送处理中提示。
3. AstrBot 按既有流程调用模型，并发送完整回答。

### 转人工

1. 用户发送匹配的触发词。
2. 插件立即停止本次 AI 回复，并调用 `kf/service_state/trans` 转交人工。
3. 填写 `servicer_userid` 时，指定成员立即接待；留空时，会话先进入待接入池，由任一接待人员在企业微信中接入。
4. 在待接入池或人工接待期间，AI 都不会回复该用户的后续消息。
5. 人工客服结束会话后，用户下次发言时插件调用 `kf/service_state/get` 确认状态并恢复 AI。

## 常见问题

### 转人工失败，或日志中出现 `95014`

通常表示接待人员未激活企业微信、未处于“正在接待”状态，或不在应用可见范围内。请先检查企业微信后台的成员和微信客服配置。

### 日志中出现 `KeyError: 'open_kfid'`

这是 AstrBot 企业微信客服适配器处理部分 `kf_msg_or_event` 事件时的已知边界问题，常见于人工客服结束聊天等客服状态事件。该事件缺少适配器预期的 `open_kfid` 字段，导致适配器记录异常。

目前已知该异常通常不影响普通用户消息、模型回复、转人工调用或插件在用户下次发言时恢复 AI 的逻辑；它主要影响触发异常的那一条客服状态事件。若后续发现消息漏处理、企业微信重复回调或其他异常，请保留脱敏日志并向 AstrBot 官方反馈。

### 转人工成功但没有成功提示

插件需要企业微信 `trans` 接口返回 `msg_code` 才能发送事件响应消息。请检查插件日志中是否存在 `kf/send_msg_on_event` 相关错误。

### 开启处理中提示后没有提示消息

确认 `send_processing_message` 已开启、`processing_message` 不为空，并检查插件日志中是否有企业微信客服 API 调用失败信息。处理中提示只会发送给未转人工的普通微信客服会话。

### 人工客服结束会话后，AI 仍不回复

确认 `verify_handoff_state` 已开启。插件会在用户下一次发言时查询会话状态；如果企业微信 API 查询失败，插件会保守地继续阻断 AI，避免抢答人工会话。

## 隐私与数据

插件仅在运行内存中保存已转人工用户的 `external_userid`，用于控制 AI 是否继续回复。该状态不会写入磁盘，重载插件或重启 AstrBot 后会清空。

## 反馈

问题反馈和功能建议请提交至本仓库的 [Issues](https://github.com/inlovewithsilver/astrbot_plugin_wecom_handoff/issues)。
