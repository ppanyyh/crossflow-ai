# CrossFlow AI V4 · LLM 增强版集成说明（第一阶段）

> **阶段定位**：本阶段仅完成「安全脚手架 + 保持旧功能可运行」。默认 **不接入任何真实 LLM**，不需要 API Key，也不产生任何外部网络请求。所有原有 Mock 功能、核心页面与既有测试保持不变。

---

## 1. 架构总览

```
07_LLM增强Demo/
├── backend/
│   ├── main.py          # FastAPI 入口（原有路由 + 新增 /api/cases/{id}/llm-draft + 静态挂载前端）
│   ├── schemas.py       # Pydantic v2 模型（原有 + LLMDraftContent / LLMDraftResponse）
│   ├── service.py       # 确定性业务逻辑（状态机 / 质检 / 审批 / resolve 门禁）—— 决策权不变
│   ├── settings.py      # ★ 新增：LLM 环境变量（LLM_BASE_URL/KEY/MODEL/ENABLED，默认关闭）
│   ├── llm_client.py    # ★ 新增：OpenAI 兼容 Chat Completions 客户端（超时 + 可识别失败）
│   ├── prompts.py       # ★ 新增：提示词（严格 JSON、禁承诺、禁编造）
│   ├── requirements.txt # 依赖清单（未新增任何依赖）
│   ├── .env.example     # ★ 新增：环境变量示例（可复制为 .env，已被忽略）
│   ├── README.md        # 原有说明（已同步 LLM 说明）
│   └── tests/
│       ├── conftest.py
│       ├── test_api.py  # 原有 25 项测试（未改动）
│       └── test_llm.py  # ★ 新增：5 项 LLM 测试
├── frontend/            # 原生 HTML/CSS/JS（新增「使用 LLM 重新生成草稿」+ 来源标签）
├── docs/
│   ├── API_CONTRACT.md  # 原有契约（已补充 4.9 llm-draft）
│   └── LLM_INTEGRATION.md  # ★ 本文件
└── .gitignore           # ★ 新增：确保 .env 不会被提交
```

### 职责边界（关键）

| 模块 | 职责 | 是否允许做最终业务决策 |
|---|---|---|
| `service.py` | 风险等级、命中规则、是否建单、是否审批、是否可结案 | ✅ 唯一决策方（确定性规则） |
| `llm_client.py` | 仅调用 OpenAI 兼容 Chat Completions | ❌ 不参与任何决策 |
| `prompts.py` | 组织提示词（只基于给定事实） | ❌ 不参与任何决策 |

**LLM 只能增强英文回复草稿与信息提取**，不得改变现有状态机。质检、主管审批、resolve 门禁仍由后端强制执行。

---

## 2. 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `LLM_ENABLED` | 否 | `false` | 是否启用 LLM。默认关闭，关闭时 LLM 接口自动回退规则模板 |
| `LLM_BASE_URL` | 启用时 | 空 | OpenAI 兼容 Chat Completions 服务的 Base URL（通常以 `/v1` 结尾） |
| `LLM_API_KEY` | 启用时 | 空 | API Key（**严禁写入代码/README/测试/前端，仅放本地 `.env`**） |
| `LLM_MODEL` | 启用时 | 空 | 模型名 |
| `LLM_TIMEOUT_SECONDS` | 否 | `10` | 每次 LLM 调用的硬超时（秒） |

配置方式（二选一）：

1. 环境变量直接设置（进程级，优先级最高）；
2. 复制 `backend/.env.example` 为 `backend/.env` 并填入值（`.env` 已被 `.gitignore` 忽略）。

> `settings.py` 内置极简 `.env` 加载器：`.env` 中的变量 **不会覆盖** 已存在的环境变量。

---

## 3. 降级策略（LLM 失败自动回退）

`POST /api/cases/{case_id}/llm-draft` 在以下任一情况自动回退到确定性规则模板 `_build_reply_draft()`，并返回 `source=fallback_template`、`fallback=true` 及具体原因：

| 触发条件 | 回退原因（示例） |
|---|---|
| `LLM_ENABLED=false` 或缺少 Key/URL/Model | `LLM 未启用或未配置…已回退规则模板` |
| 网络错误 / 连接失败 | `LLM 网络错误：…已回退规则模板` |
| 调用超时 | `LLM 调用超时，已回退规则模板` |
| 服务返回非 2xx | `LLM 服务返回 HTTP 500，已回退规则模板` |
| 返回体结构错误 / 内容为空 | `LLM 返回格式错误：…已回退规则模板` |
| 内容不是合法 JSON | `LLM 返回非法 JSON，已回退规则模板` |
| JSON 不符合结构化模型（含额外字段/空值/超长） | `LLM 返回内容不符合结构化模型，已回退规则模板` |
| JSON 含禁用业务决策字段（risk_level / approval / resolve / ticket 等） | `LLM 输出含禁用业务决策字段（…），已回退规则模板` |
| 草稿安全预校验失败（越权承诺 / 错误订单号 / 虚构签收 / 自动结案语义） | `LLM 草稿安全校验失败，已回退规则模板：…` |

**关键保证**：任何 LLM 失败都不会抛出未处理异常、不会崩溃服务、不会改变工单/会话/质检门禁状态；`llm_client.py` 对每种失败都返回可识别的 `LLMCallResult(success=False, error=...)`。

---

## 4. 安全边界

1. **不写真实 Key**：代码、README、测试、前端中均无真实 API Key；Key 仅通过环境变量或本地 `backend/.env`（已忽略）注入。
2. **提示词硬约束**（`prompts.py`）：
   - 只能基于传入的客户消息、订单事实、物流事实、规则摘要生成内容；
   - 禁止承诺退款金额、退款时效、补发已执行、保证/确定送达时间；
   - 禁止编造订单、物流、政策或处理结果；
   - 输出严格 JSON，不带 Markdown 代码围栏。
3. **LLM 输出不含最终业务决策字段**：结构化模型 `LLMDraftContent` 设 `extra="forbid"`，只接受 `intent / sentiment / extracted_order_id / missing_information / reply_draft / disclaimer`，**拒绝任何额外字段**（包括 `risk_level / handoff_required / approval / resolve / ticket / refund_decision` 等）。`reply_draft / intent / disclaimer` 还施加长度限制，避免空值或异常超长文本。
4. **决策权不变**：风险、规则、建单、审批、结案全部由 `service.py` 确定性规则决定。

### 两层防线（关键区分）

| 防线 | 位置 | 时机 | 有无副作用 | 作用 |
|---|---|---|---|---|
| **① LLM 返回前预校验** | `service.validate_llm_draft_safety()` | LLM 草稿被采纳前 | **无副作用**（不改会话/工单/审批/质检状态，不依赖审批状态） | 让不合规、越权、伪造事实的输出在返回前自动降级为规则模板 |
| **② 发送前 AI 质检门禁** | `POST /api/quality-check` + `resolve` 门禁 | 客服确认发送前 | 有副作用（`overall_passed=true` 写入 `quality_passed` 门禁标记） | 作为最终防线，拦截任何绕过多级预校验的越权回复，未通过不得结案 |

`validate_llm_draft_safety()` 是**纯校验**，只读案例事实，逐项检查草稿正文：

1. 不得命中 `FORBIDDEN_RE` 中的未经授权承诺（退款金额/时效、补发已执行、保证送达时间等）；
2. 有订单号时，草稿中出现的任何 `CF` 订单号必须与案例订单号一致；
3. 无订单号时，草稿不得编造任何 `CF` 订单号；
4. 物流并非 `delivered` 时，草稿不得声称已签收/已送达；
5. 不得包含最终业务决策 / 自动退款 / 自动补发 / 自动结案语义。

任一检查失败即回退：`source=fallback_template`、`fallback=true`、`fallback_reason="LLM 草稿安全校验失败，已回退规则模板：具体原因"`。**该预校验不会调用 `quality_check()`**（后者依赖审批状态并写入门禁标记），二者职责严格分离。


---

## 5. 启动方法

```bash
cd "F:\跨境电商售后智能协同工作台\07_LLM增强Demo\backend"

# 创建并激活虚拟环境（Python 3.12）
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell
# 或 source .venv/Scripts/activate   # Git Bash

# 安装依赖
pip install -r requirements.txt

# 启动（默认 http://127.0.0.1:8000）
python -m uvicorn main:app --reload --port 8000
```

- 前端工作台：http://127.0.0.1:8000/
- Swagger UI：http://127.0.0.1:8000/docs

---

## 6. 未配置 API Key 时的演示方式（默认即可演示）

**无需任何 Key**，服务即可完整运行并演示：

1. 默认 `LLM_ENABLED=false`，`POST /api/cases/{case_id}/llm-draft` 会返回 `source=fallback_template`，`fallback=true`，原因是「LLM 未启用或未配置」——演示了自动回退。
2. 前端会话详情页「英文回复草稿」卡片新增 **「使用 LLM 重新生成草稿」** 按钮：未配置 Key 时点击，会以「规则模板降级结果」标签 + 友好提示回退，**并保留当前规则草稿**。
3. 若配置了 Key，同一按钮会显示「LLM 生成，需客服复核」标签，草稿替换为 LLM 输出（仍须通过 AI 质检才能结案）。

### 前端来源标签说明

| 场景 | 标签文案 |
|---|---|
| LLM 成功返回合规结构化草稿 | `LLM 生成，需客服复核` |
| LLM 失败 / 未配置 / 超时 / 非法 JSON | `规则模板降级结果`（附回退原因） |
| 请求异常（网络等） | 友好提示 + 保留当前规则草稿 |

---

## 7. 新增接口

### `POST /api/cases/{case_id}/llm-draft`

- 仅返回草稿生成结果与来源，**不改变工单或会话状态**；
- 响应字段：`case_id`、`source`（`llm` / `fallback_template`）、`fallback`（bool）、`fallback_reason`、`draft`（`LLMDraftContent`）。

响应示例（回退时）：

```json
{
  "case_id": "CF-DEV-002",
  "source": "fallback_template",
  "fallback": true,
  "fallback_reason": "LLM 未启用或未配置（LLM_ENABLED=false / 缺少 Key），已回退规则模板",
  "draft": {
    "intent": "tracking_stalled_refund_request",
    "sentiment": "negative",
    "extracted_order_id": "CF20260926001",
    "missing_information": [],
    "reply_draft": "Hi Emma, ...",
    "disclaimer": "本草稿为规则模板生成，仅供参考，需客服复核；不确认任何退款、补发或送达时间。"
  }
}
```

---

## 8. 测试

```bash
cd "F:\跨境电商售后智能协同工作台\07_LLM增强Demo\backend"
python -m pytest tests/ -v
```

共 **37 项**：原有 25 项（`test_api.py`，未改动，全部通过）+ 12 项 LLM 测试（`test_llm.py`，含阶段 1.1 新增 5 项安全加固用例与 V1.6 新增 2 项撇号空格规范化用例）：

| 测试 | 覆盖场景 |
|---|---|
| `test_llm_disabled_falls_back_to_template` | LLM 未启用时自动回退规则模板 |
| `test_llm_success_returns_compliant_draft` | LLM 成功返回合规结构化草稿 |
| `test_llm_timeout_falls_back` | LLM 超时回退 |
| `test_llm_invalid_json_falls_back` | LLM 返回非法 JSON 回退 |
| `test_llm_unauthorized_promise_falls_back_and_final_gate_still_enforced` | 越权草稿返回前降级；直接提交越权回复时质检 + resolve 门禁仍拦截 |
| `test_llm_forbidden_decision_fields_fall_back` | LLM 输出含 risk_level / approval 等禁用字段自动回退 |
| `test_llm_refund_reship_promise_falls_back` | LLM 草稿承诺退款金额/时效/补发已执行自动回退 |
| `test_llm_wrong_order_number_falls_back` | LLM 草稿含错误订单号自动回退 |
| `test_llm_fabricated_delivered_falls_back` | LLM 草稿虚构已签收/已送达而物流不支持自动回退 |
| `test_llm_final_decision_semantics_falls_back` | LLM 草稿含最终业务决策/自动结案语义自动回退 |

> 这些测试通过 mock `settings.llm_configured` 与 `llm_client.chat_completion` 模拟 LLM 各种结果，全程不接入真实 LLM、不读取任何真实 Key。

### 真实 DeepSeek 联调验证（V1.6）

对 `CF-DEV-001` 执行了一次真实 DeepSeek 联调，仅核对脱敏结果，未记录任何 Key、请求内容或完整草稿：

- 已验证 HTTP 200；
- `source` = `llm`；
- `fallback` = `false`；
- `reply_draft` 非空；
- 返回草稿通过 `quality-check`；
- 高风险流程仍受人工审批与质检门禁约束；
- 未在代码、文档、日志或报告中记录 API Key。

---

## 9. 已知限制

1. 本阶段 LLM 默认关闭，未内置任何真实模型或 Key；
2. `llm_client.py` 仅支持 OpenAI 兼容 `/chat/completions` 的同步调用（用标准库 `urllib` 实现，未新增依赖）；
3. LLM 草稿不改变任何业务决策，最终结案仍受确定性规则 + 质检门禁约束；
4. 其余限制与纯 Mock 版一致（内存状态、规则引擎质检、无鉴权等，见 `backend/README.md`）。
