# CrossFlow AI V4 Mock API Contract

## 1. 目的与边界

本契约用于 `06_可运行Demo` 的本地 Mock MVP。服务只读取项目根目录 `data/` 的模拟数据，不连接真实 Shopify、Amazon、物流商、支付系统或客户数据。

第一版不依赖大模型：AI 决策由案例预期结果与售后规则确定性生成，以保证结果可复现、可演示、可测试。V3 是独立归档项目，本服务不得修改或依赖 V3 才能运行。

## 2. 通用约定

- Base URL：`http://127.0.0.1:8000`
- 响应格式：JSON
- `case_id` 对应根目录 `data/cases_dev.json` 中的案例；
- 订单、物流和规则均从根目录 `data/` 读取；
- 状态取值：`待处理`、`待客户补充`、`待人工审核`、`处理中`、`已解决`、`已关闭`；
- 风险取值：`低`、`中`、`高`；多个规则命中时取最高风险等级；
- 高风险案例禁止直接返回“自动退款”“自动补发”或“可直接关闭”的动作。

## 3. 数据对象

### 3.1 CaseSummary

```json
{
  "case_id": "CF-DEV-002",
  "customer_alias": "Emma R.",
  "country": "United States",
  "issue_type": "物流停滞 + 退款要求",
  "order_id": "CF20260926001",
  "risk_level": "高",
  "conversation_status": "待处理",
  "updated_at": "刚刚 · 10:10"
}
```

### 3.2 CaseDetail

```json
{
  "case_id": "CF-DEV-002",
  "customer_message": "My package hasn't moved for 12 days. I need a refund now.",
  "customer": {"alias": "Emma R.", "country": "United States", "language": "English"},
  "order": {},
  "logistics": {},
  "ai_decision": {
    "intent": "tracking_stalled_refund_request",
    "sentiment": "negative",
    "risk_level": "高",
    "applied_rule_ids": ["L-03", "L-06"],
    "applied_rule_names": ["严重物流停滞", "退款、赔偿或保证时效施压"],
    "recommended_action": "create_manual_review_ticket",
    "handoff_required": true,
    "reply_draft": "..."
  },
  "conversation_status": "待处理",
  "ticket": null,
  "quality_passed": false
}
```

`quality_passed` 为质检通过门禁标记：仅当该案例最近一次 `POST /api/quality-check` 返回 `overall_passed=true` 时为 `true`。中/高风险（或 `handoff_required=true`）案例在 `resolve` 前必须满足该标记。

### 3.3 Ticket

```json
{
  "ticket_id": "TKT-001",
  "case_id": "CF-DEV-002",
  "reason": "物流停滞且客户要求退款",
  "risk_level": "高",
  "status": "待人工审核",
  "agent_note": "客户已多次催促退款，建议优先审核。",
  "supervisor_decision": null,
  "supervisor_note": null,
  "audit_log": []
}
```

## 4. API

### 4.1 获取会话列表

`GET /api/cases`

可选 query 参数：`status`、`risk_level`。

响应：`CaseSummary[]`。

### 4.2 获取会话详情

`GET /api/cases/{case_id}`

响应：`CaseDetail`。如果案例不存在，返回 HTTP 404。

### 4.3 创建人工审核工单

`POST /api/tickets`

请求：

```json
{
  "case_id": "CF-DEV-002",
  "agent_note": "客户已多次催促退款，建议优先审核。"
}
```

规则：仅 `handoff_required=true` 的案例可创建工单；重复创建应返回现有工单，不创建第二张。

响应：`Ticket`，状态必须为 `待人工审核`。

### 4.4 获取工单列表

`GET /api/tickets`

可选 query 参数：`status`。

响应：`Ticket[]`。

### 4.5 主管审批

`POST /api/tickets/{ticket_id}/approve`

请求：

```json
{
  "decision": "批准补发建议",
  "supervisor_note": "物流停滞超过12天，批准补发建议。"
}
```

允许 decision：`要求补充材料`、`批准补发建议`、`批准退款建议`、`驳回并改写回复`。

响应：更新后的 `Ticket`。成功审批后，工单与关联会话状态更新为 `处理中`，并写入 `audit_log`。

### 4.6 发送低风险回复 / 补充信息请求

`POST /api/cases/{case_id}/resolve`

请求：

```json
{
  "action": "send_reply",
  "reply": "Hi Liam, ..."
}
```

规则：低风险物流咨询可更新为 `已解决`；订单号缺失案例的 action 为 `request_order_information`，状态更新为 `待客户补充`；高风险案例未完成审批时不得 resolve。

质检门禁（V2.1）：中/高风险（或 `handoff_required=true`）案例除必须「已建单、已审批」外，还必须满足 `quality_passed=true`（即最近一次质检 `overall_passed=true`）才能 `resolve`，否则返回 HTTP 403 与中文错误信息。低风险直接结案与「订单号缺失 → 待客户补充」分支不受该门禁约束。

响应：更新后的 `CaseDetail`。

### 4.7 AI 质检

`POST /api/quality-check`

请求：

```json
{
  "case_id": "CF-DEV-002",
  "reply": "Hi Emma, ..."
}
```

响应：

```json
{
  "case_id": "CF-DEV-002",
  "checks": [
    {"name": "订单与物流事实一致", "passed": true},
    {"name": "无未经授权承诺", "passed": true},
    {"name": "高风险已完成主管审核", "passed": true},
    {"name": "包含明确的后续处理说明", "passed": true}
  ],
  "overall_passed": true
}
```

最低校验：禁止承诺退款金额、退款时效、补发已执行、确定送达时间；高风险案例必须已审批。

副作用（V2.1）：`overall_passed=true` 时将该案例的 `quality_passed` 状态写入内存标记；`overall_passed=false` 时清除该标记。该标记用于 `resolve` 门禁（见 4.6）。

### 4.8 获取运营看板

`GET /api/dashboard`

响应字段：

```json
{
  "today_resolved": 13,
  "ai_adoption_rate": 80,
  "human_intervention_rate": 33,
  "reply_compliance_rate": 97,
  "issue_distribution": [
    {"label": "物流延误", "value": 42},
    {"label": "签收未收到", "value": 25},
    {"label": "地址异常", "value": 18},
    {"label": "其他", "value": 15}
  ],
  "risk_distribution": [
    {"label": "高风险", "value": 18},
    {"label": "中风险", "value": 32},
    {"label": "低风险", "value": 50}
  ],
  "simulated": true
}
```

## 5. 接口与 Axure 页面映射

| Axure 页面 | 主要接口 |
|---|---|
| 01 工作台首页 | `GET /api/cases`、`GET /api/dashboard` |
| 02 会话中心 | `GET /api/cases` |
| 03 高风险会话详情 | `GET /api/cases/{case_id}`、`POST /api/tickets` |
| 05 工单中心 | `GET /api/tickets` |
| 06 主管审批 | `POST /api/tickets/{ticket_id}/approve` |
| 07 AI 质检 | `POST /api/quality-check` |
| 08 运营看板 | `GET /api/dashboard` |
| 09 低风险分支 | `POST /api/cases/{case_id}/resolve` |
| 10 订单号缺失分支 | `POST /api/cases/{case_id}/resolve` |
| 11 签收争议分支 | `GET /api/cases/{case_id}`、`POST /api/tickets` |

## 6. 验收要求

- 服务在未配置任何外部 API Key 时能够运行；
- 所有接口返回 JSON 且有 Pydantic 校验；
- 数据源路径从当前文件位置计算，不依赖终端当前目录；
- 高风险、低风险、信息不足三种分流结果与 `data/cases_dev.json` 一致；
- 真实接口、真实数据、自动退款或自动补发均不在本期范围内；
- README 必须包含启动、测试和“模拟数据 / 非生产系统”说明。
