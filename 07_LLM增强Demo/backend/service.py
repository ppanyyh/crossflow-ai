"""Business logic for the CrossFlow AI V4 Mock backend.

Design principles (see docs/API_CONTRACT.md):
- Only reads simulated JSON under `<project root>/data/`; the path is derived
  from this file's location, never from the current working directory.
- No real Shopify / Amazon / carrier / payment / LLM integration.
- AI decisions are deterministic: they are derived from each case's expected
  result in `cases_dev.json` plus the after-sales rules, so results are
  reproducible and demo/test friendly.
- Never performs or promises auto-refund, auto-reshipment, guaranteed delivery
  dates or compensation amounts.

In-memory state (conversation statuses + tickets) is reset on process start;
this is a mock for a portfolio prototype, not a production system.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

import llm_client
import prompts
import settings
from schemas import (
    CaseDetail,
    CaseSummary,
    CustomerInfo,
    AIDecision,
    DashboardResponse,
    DistributionItem,
    LLMDraftContent,
    LLMDraftResponse,
    QualityCheckResponse,
    Ticket,
)

# ---------------------------------------------------------------------------
# Paths: derived from this file, independent of the working directory.
# service.py lives at <root>/06_Can-run Demo/backend/service.py
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent          # .../06_Can-run Demo/backend
PROJECT_ROOT = BACKEND_DIR.parents[1]                   # .../Cross-border E-commerce After-sales Intelligent Collaboration Workbench
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Static mappings
# ---------------------------------------------------------------------------

RISK_CN = {"low": "低", "medium": "中", "high": "高"}

INTENT_ISSUE_TYPE = {
    "tracking_delay": "物流延误",
    "tracking_stalled": "物流停滞",
    "tracking_stalled_refund_request": "物流停滞 + 退款要求",
    "tracking_status_request": "物流状态咨询",
    "tracking_not_created": "物流单未生成",
    "order_status_request": "订单状态查询",
    "invalid_order_number": "订单号无效",
    "delivered_not_received": "签收未收到",
    "delivered_not_received_reship_request": "签收未收到 + 补发要求",
    "compensation_pressure": "赔偿施压",
    "address_exception": "地址异常",
    "privacy_or_other_order": "隐私 / 他人订单查询",
}

COUNTRY_LANGUAGE = {
    "United States": "English",
    "United Kingdom": "English",
    "Australia": "English",
    "Canada": "English",
    "Germany": "German",
    "France": "French",
    "Spain": "Spanish",
}

FALLBACK_CUSTOMER = {"alias": "未匹配客户", "country": "未知", "language": "English"}

ALLOWED_APPROVE_DECISIONS = (
    "要求补充材料",
    "批准补发建议",
    "批准退款建议",
    "驳回并改写回复",
)

# Order numbers look like CF + 10+ digits (e.g. CF20260926001).
ORDER_PATTERN = re.compile(r"CF\d{8,}")

# Patterns that indicate an unauthorized commitment (case-insensitive).
FORBIDDEN_PATTERNS = [
    r"refund\s+(will|would)\s+be\s+(issued|processed|approved)",
    r"we\s+(will|have)\s+(issued?|process(?:ed)?|approv(?:e|ed))\s+(a\s+)?refund",
    r"full\s+refund",
    r"money\s+back",
    r"refund\s+(of\s+)?\$?\s?\d",
    r"\$\s?\d+(?:\.\d+)?",
    r"compensation\s+(approved|of\s+\$|will\s+be)",
    r"reship",
    r"replacement\s+(has\s+been|will\s+be)\s+(sent|shipped|dispatched)",
    r"replacement\s+confirmed",
    r"we\s+(will|have)\s+sen[dt]\s+(a\s+)?(new\s+)?(package|replacement|item)",
    r"guarantee[d]?\s+(delivery|arrival)",
    r"will\s+arrive\s+(before|by|on|next)",
    r"arrive\s+before",
    r"deliver(?:y|ed)?\s+(by\s|tomorrow)",
    r"refund\s+today",
]
FORBIDDEN_RE = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_PATTERNS]

# Keywords that indicate a reply contains clear next-step instructions.
NEXT_STEP_KEYWORDS = (
    "review",
    "check",
    "monitor",
    "confirm",
    "provide",
    "contact",
    "update",
    "look into",
    "investigat",
    "we will",
    "get back to you",
)

# A reply that claims the parcel was delivered while carrier data disagrees.
DELIVERED_CLAIM_RE = re.compile(r"(has\s+been|was|marked\s+as)\s+delivered", re.IGNORECASE)

# LLM 结构化输出中禁止出现的最终业务决策字段。风险等级、命中规则、是否建单、
# 是否审批、是否可结案等决策只能由确定性规则产生，LLM 一旦输出这些字段即视为越权。
FORBIDDEN_DECISION_FIELDS = {
    "risk_level",
    "handoff_required",
    "approval",
    "approved",
    "resolve",
    "resolved",
    "ticket",
    "refund_decision",
    "decision",
    "recommended_action",
}

# 草稿正文中不得出现的「最终业务决策 / 自动退款 / 自动补发 / 自动结案」语义
# （与上方 FORBIDDEN_RE 的「未经授权承诺」互补：这里聚焦自动执行与结案结论）。
DECISION_SEMANTIC_PATTERNS = [
    r"auto(?:matic(?:ally)?)?[-\s]?(?:refund|reship|resend|replacement|compensat|close|resolve)",
    r"(?:refund|replacement|reshipment)\s+(?:has\s+been|was|is)\s+(?:approved|confirmed|processed|granted)",
    r"(?:your\s+)?case\s+(?:is|has\s+been)\s+(?:closed|resolved|settled)",
    r"(?:your\s+)?(?:request|refund|claim)\s+(?:is|has\s+been)\s+(?:approved|resolved|closed|settled)",
    r"will\s+(?:now\s+)?(?:close|resolve|settle)\s+(?:your\s+)?case",
]
DECISION_SEMANTIC_RE = [re.compile(p, re.IGNORECASE) for p in DECISION_SEMANTIC_PATTERNS]

# 英文缩写撇号相邻异常空格的规范化：仅当撇号两侧为英文字母且中间存在多余空格时
# 移除该空格，例如 "I' m" -> "I'm"、"hasn' t" -> "hasn't"、"order' s" -> "order's"。
# 覆盖 ASCII 单引号 (') 与 Unicode 右单引号 (U+2019)。不触碰订单号、金额、物流
# 事实或其他业务内容。
_APOSTROPHE_CHARS = "'\u2019"

_APOS_TRAILING_SPACE_RE = re.compile(
    rf"([A-Za-z])([{_APOSTROPHE_CHARS}])\s+([A-Za-z])"
)
_APOS_LEADING_SPACE_RE = re.compile(
    rf"([A-Za-z])\s+([{_APOSTROPHE_CHARS}])(m|re|ve|ll|d|s|t)\b"
)


def normalize_apostrophe_spacing(text: str) -> str:
    """Remove stray spaces around English contraction apostrophes.

    Only spaces directly adjacent to an apostrophe are removed when they sit
    between letters (e.g. "I' m" -> "I'm", "hasn' t" -> "hasn't"). Order
    numbers, amounts, logistics facts and other business content are never
    modified.
    """
    if not text:
        return text
    text = _APOS_TRAILING_SPACE_RE.sub(r"\1\2\3", text)
    text = _APOS_LEADING_SPACE_RE.sub(r"\1\2\3", text)
    return text


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NotFoundError(Exception):
    """Resource does not exist -> HTTP 404."""


class BusinessError(Exception):
    """Business rule violation -> HTTP 400/403/422."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_json(filename: str) -> Any:
    path = DATA_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Reply draft generation (deterministic, rule-compliant)
# ---------------------------------------------------------------------------


def _build_reply_draft(case: dict, customer: dict) -> str:
    alias = customer["alias"]
    exp = case["expected"]
    action = exp["recommended_action"]
    order_id = exp.get("order_id")
    logistics = _state.logistics_by_order.get(order_id) if order_id else None

    if action == "send_tracking_update" and logistics:
        return (
            f"Hi {alias}, thank you for reaching out. Here is the latest carrier update "
            f"for your order {order_id}: \"{logistics.get('last_event', 'No event recorded')}\" "
            f"(carrier: {logistics.get('carrier')}, updated: {logistics.get('last_update')}). "
            f"Your parcel is still in transit and moving through the network. "
            f"We will keep monitoring the shipment and share any new updates with you."
        )

    if action == "send_order_preparation_update":
        return (
            f"Hi {alias}, thank you for your patience. Your order is currently being "
            f"prepared in our warehouse, and the shipping label will be created once it "
            f"is packed. We will send you the tracking number as soon as the parcel is "
            f"handed over to the carrier."
        )

    if action == "request_order_information":
        return (
            f"Hi {alias}, thank you for your message. To locate your order, could you "
            f"please provide your full order number? It starts with \"CF\" and can be "
            f"found in your order confirmation email. Once we have it, we will check the "
            f"status for you right away."
        )

    if action == "create_manual_review_ticket":
        return (
            f"Hi {alias}, we are truly sorry for the delay with your order. Your case has "
            f"been escalated to our specialist team for manual review, and we will check "
            f"the full logistics record. We will get back to you with the review result as "
            f"soon as possible. No outcome will be confirmed before the review is complete."
        )

    if action == "supervisor_review":
        return (
            f"Hi {alias}, thank you for reaching out. Given the situation of your order, "
            f"your case has been routed to a supervisor for review. A supervisor will "
            f"check the order and logistics details and contact you with the next steps. "
            f"We cannot confirm any outcome before the review is complete."
        )

    if action == "collect_delivery_evidence_and_review":
        return (
            f"Hi {alias}, we are sorry to hear that the parcel shows delivered but was not "
            f"received. Could you please confirm your delivery address and check nearby "
            f"locations such as the lobby, mailbox or neighbours? We will review the "
            f"carrier's delivery record and get back to you with the result. We cannot "
            f"promise a replacement or refund before the review is complete."
        )

    if action == "request_address_confirmation":
        return (
            f"Hi {alias}, the carrier reported that they could not verify the delivery "
            f"address. For security reasons we cannot display the stored full address "
            f"here. Please confirm your shipping address through an approved channel (for "
            f"example the verified email on your account), so the carrier can schedule a "
            f"new delivery attempt."
        )

    if action == "refuse_and_handoff":
        return (
            f"Hi {alias}, we are sorry, but for privacy reasons we cannot share any order "
            f"details when the request does not come from the verified account holder. "
            f"Please ask the account owner to contact us through a verified channel, and "
            f"we will be glad to help."
        )

    # Fallback: safe, review-oriented draft.
    return (
        f"Hi {alias}, thank you for your message. Our team will review your case and "
        f"get back to you with an update as soon as possible."
    )


# ---------------------------------------------------------------------------
# Application state (in-memory)
# ---------------------------------------------------------------------------


class AppState:
    def __init__(self) -> None:
        self.cases: list[dict] = _load_json("cases_dev.json")
        self.orders_by_id: dict[str, dict] = {
            o["order_id"]: o for o in _load_json("orders.json")
        }
        self.logistics_by_order: dict[str, dict] = {
            l["order_id"]: l for l in _load_json("logistics.json")
        }
        self.rules_by_id: dict[str, dict] = {
            r["rule_id"]: r for r in _load_json("after_sales_rules.json")
        }
        self.case_status: dict[str, str] = {c["case_id"]: "待处理" for c in self.cases}
        # 质检通过门禁：overall_passed=true 时才写入 True，作为高风险结案的前置条件。
        self.quality_passed: dict[str, bool] = {}
        self.tickets: list[dict] = []
        self._ticket_seq = 0

    def reset(self) -> None:
        """Restore initial statuses / clear tickets (used by tests)."""
        self.case_status = {c["case_id"]: "待处理" for c in self.cases}
        self.quality_passed = {}
        self.tickets = []
        self._ticket_seq = 0


_state = AppState()
# Public alias (also used by tests to reset in-memory state).
state = _state

# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------


def _get_case(case_id: str) -> dict:
    for case in _state.cases:
        if case["case_id"] == case_id:
            return case
    raise NotFoundError(f"案例不存在: {case_id}")


def _customer_of(case: dict) -> dict:
    order_id = case["expected"].get("order_id")
    order = _state.orders_by_id.get(order_id) if order_id else None
    if order:
        return {
            "alias": order["customer_alias"],
            "country": order["country"],
            "language": COUNTRY_LANGUAGE.get(order["country"], "English"),
        }
    return dict(FALLBACK_CUSTOMER)


def _ai_decision_of(case: dict) -> AIDecision:
    exp = case["expected"]
    rule_ids = list(exp.get("rule_ids", []))
    rule_names = [
        _state.rules_by_id[rid]["name"] for rid in rule_ids if rid in _state.rules_by_id
    ]
    customer = _customer_of(case)
    return AIDecision(
        intent=exp["intent"],
        sentiment=exp["sentiment"],
        risk_level=RISK_CN[exp["risk_level"]],
        applied_rule_ids=rule_ids,
        applied_rule_names=rule_names,
        recommended_action=exp["recommended_action"],
        handoff_required=bool(exp["handoff_required"]),
        reply_draft=_build_reply_draft(case, customer),
    )


def _rules_summary_of(case: dict) -> list[dict]:
    """命中规则的摘要（仅供 LLM 参考，不含最终业务决策字段）。"""
    summary = []
    for rid in case["expected"].get("rule_ids", []):
        rule = _state.rules_by_id.get(rid)
        if rule is None:
            continue
        summary.append(
            {
                "rule_id": rule["rule_id"],
                "name": rule["name"],
                "reply_requirements": rule.get("reply_requirements", []),
            }
        )
    return summary


def _parse_llm_json(content: str) -> Optional[dict]:
    """解析 LLM 文本响应为 dict，容忍 Markdown 代码围栏。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _ticket_of_case(case_id: str) -> Optional[dict]:
    for ticket in _state.tickets:
        if ticket["case_id"] == case_id:
            return ticket
    return None


def _audit(actor: str, action: str, detail: str) -> dict:
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "actor": actor,
        "action": action,
        "detail": detail,
    }


def _updated_at(case_id: str) -> str:
    idx = next(
        (i for i, c in enumerate(_state.cases) if c["case_id"] == case_id), 0
    )
    return f"刚刚 · {9 + (idx % 9):02d}:{(idx * 17) % 60:02d}"


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------


def list_case_summaries(status: Optional[str], risk_level: Optional[str]) -> list[dict]:
    summaries = []
    for case in _state.cases:
        decision = _ai_decision_of(case)
        summaries.append(
            CaseSummary(
                case_id=case["case_id"],
                customer_alias=_customer_of(case)["alias"],
                country=_customer_of(case)["country"],
                issue_type=INTENT_ISSUE_TYPE.get(
                    case["expected"]["intent"], "其他"
                ),
                order_id=case["expected"].get("order_id"),
                risk_level=decision.risk_level,
                conversation_status=_state.case_status[case["case_id"]],
                updated_at=_updated_at(case["case_id"]),
            ).model_dump()
        )
    if status is not None:
        summaries = [s for s in summaries if s["conversation_status"] == status]
    if risk_level is not None:
        summaries = [s for s in summaries if s["risk_level"] == risk_level]
    return summaries


def get_case_detail(case_id: str) -> dict:
    case = _get_case(case_id)
    customer = _customer_of(case)
    order_id = case["expected"].get("order_id")
    order = _state.orders_by_id.get(order_id, {}) if order_id else {}
    logistics = _state.logistics_by_order.get(order_id, {}) if order_id else {}
    ticket = _ticket_of_case(case_id)
    detail = CaseDetail(
        case_id=case["case_id"],
        customer_message=case["customer_message"],
        customer=CustomerInfo(**customer),
        order=order,
        logistics=logistics,
        ai_decision=_ai_decision_of(case),
        conversation_status=_state.case_status[case["case_id"]],
        ticket=Ticket(**ticket) if ticket else None,
        quality_passed=_state.quality_passed.get(case_id, False),
    )
    return detail.model_dump()


def create_ticket(case_id: str, agent_note: Optional[str]) -> dict:
    case = _get_case(case_id)
    decision = _ai_decision_of(case)

    if not decision.handoff_required:
        raise BusinessError(
            "该案例风险等级低、无需人工审核，不允许创建工单", status_code=400
        )

    existing = _ticket_of_case(case_id)
    if existing is not None:
        return existing  # idempotent: never create a second ticket

    _state._ticket_seq += 1
    reason = "、".join(decision.applied_rule_names) or INTENT_ISSUE_TYPE.get(
        case["expected"]["intent"], "人工审核"
    )
    ticket = {
        "ticket_id": f"TKT-{_state._ticket_seq:03d}",
        "case_id": case_id,
        "reason": reason,
        "risk_level": decision.risk_level,
        "status": "待人工审核",
        "agent_note": agent_note,
        "supervisor_decision": None,
        "supervisor_note": None,
        "audit_log": [
            _audit("agent", "创建工单", f"客服为案例 {case_id} 创建人工审核工单：{reason}")
        ],
    }
    _state.tickets.append(ticket)
    _state.case_status[case_id] = "待人工审核"
    # 建单意味着重新进入人工审核流程，作废旧的质检通过标记。
    _state.quality_passed.pop(case_id, None)
    return ticket


def list_tickets(status: Optional[str]) -> list[dict]:
    tickets = _state.tickets
    if status is not None:
        tickets = [t for t in tickets if t["status"] == status]
    return tickets


def approve_ticket(ticket_id: str, decision: str, supervisor_note: Optional[str]) -> dict:
    ticket = next((t for t in _state.tickets if t["ticket_id"] == ticket_id), None)
    if ticket is None:
        raise NotFoundError(f"工单不存在: {ticket_id}")

    if ticket["supervisor_decision"] is not None:
        raise BusinessError("该工单已完成审批，不能重复审批", status_code=400)

    ticket["supervisor_decision"] = decision
    ticket["supervisor_note"] = supervisor_note
    ticket["status"] = "处理中"
    ticket["audit_log"].append(
        _audit("supervisor", "主管审批", f"审批意见：{decision}；备注：{supervisor_note or '无'}")
    )
    _state.case_status[ticket["case_id"]] = "处理中"
    # 审批推进到「处理中」后，需基于最终回复重新质检，故清除旧的质检通过标记。
    _state.quality_passed.pop(ticket["case_id"], None)
    return ticket


def resolve_case(case_id: str, action: str, reply: Optional[str]) -> dict:
    case = _get_case(case_id)
    exp = case["expected"]
    decision = _ai_decision_of(case)

    if action == "send_reply":
        if not reply or not reply.strip():
            raise BusinessError("send_reply 必须提供非空回复内容", status_code=422)
        if decision.risk_level in ("中", "高") or decision.handoff_required:
            ticket = _ticket_of_case(case_id)
            if ticket is None or ticket["supervisor_decision"] is None:
                raise BusinessError(
                    "中/高风险案例未经主管审批，不能直接解决", status_code=403
                )
            if not _state.quality_passed.get(case_id, False):
                raise BusinessError(
                    "中/高风险案例须先通过 AI 质检（quality_passed=true）才能结案，"
                    "请先对回复运行质检并确保 overall_passed=true",
                    status_code=403,
                )
        _state.case_status[case_id] = "已解决"
        ticket = _ticket_of_case(case_id)
        if ticket is not None:
            ticket["status"] = "已关闭"
            ticket["audit_log"].append(
                _audit("agent", "会话解决", f"案例 {case_id} 已发送回复并解决，工单关闭")
            )
    else:  # request_order_information
        needs_info = (
            exp["recommended_action"] == "request_order_information"
            or exp.get("order_id") is None
        )
        if not needs_info:
            raise BusinessError(
                "该案例不属于订单号缺失场景，不能发起补充订单信息请求", status_code=400
            )
        _state.case_status[case_id] = "待客户补充"

    return get_case_detail(case_id)


def quality_check(case_id: str, reply: str) -> dict:
    case = _get_case(case_id)
    exp = case["expected"]
    decision = _ai_decision_of(case)

    if not reply or not reply.strip():
        raise BusinessError("质检必须提供非空回复内容", status_code=422)

    # 1. 订单与物流事实一致
    mentioned_orders = ORDER_PATTERN.findall(reply)
    expected_order = exp.get("order_id")
    if expected_order:
        fact_ok = all(o == expected_order for o in mentioned_orders)
    else:
        fact_ok = len(mentioned_orders) == 0
    logistics = _state.logistics_by_order.get(expected_order, {}) if expected_order else {}
    if logistics and logistics.get("status") != "delivered":
        if DELIVERED_CLAIM_RE.search(reply):
            fact_ok = False
    if any(o not in _state.orders_by_id for o in mentioned_orders):
        fact_ok = False

    # 2. 无未经授权承诺
    promise_ok = not any(rx.search(reply) for rx in FORBIDDEN_RE)

    # 3. 高风险已完成主管审核
    if decision.risk_level == "高":
        ticket = _ticket_of_case(case_id)
        review_ok = ticket is not None and ticket["supervisor_decision"] is not None
    else:
        review_ok = True

    # 4. 包含明确的后续处理说明
    lowered = reply.lower()
    next_step_ok = any(k in lowered for k in NEXT_STEP_KEYWORDS)

    checks = [
        {"name": "订单与物流事实一致", "passed": fact_ok},
        {"name": "无未经授权承诺", "passed": promise_ok},
        {"name": "高风险已完成主管审核", "passed": review_ok},
        {"name": "包含明确的后续处理说明", "passed": next_step_ok},
    ]
    result = QualityCheckResponse(
        case_id=case_id,
        checks=checks,
        overall_passed=all(c["passed"] for c in checks),
    )
    # 质检通过才写入门禁标记；未通过则清除，避免旧的通过标记残留。
    if result.overall_passed:
        _state.quality_passed[case_id] = True
    else:
        _state.quality_passed.pop(case_id, None)
    return result.model_dump()


def validate_llm_draft_safety(
    case: dict, content: LLMDraftContent
) -> tuple[bool, Optional[str]]:
    """LLM 草稿返回前的纯安全预校验（无副作用）。

    只做内容安全预筛，不修改会话、工单、审批或质检门禁状态，也不依赖审批
    状态——这是与 ``quality_check()`` 的关键区别：``quality_check`` 会依赖
    审批状态并写入 ``quality_passed`` 门禁标记，而本函数完全无副作用，仅决定
    LLM 草稿是否可被采纳为 ``source=llm`` 返回；不通过则由调用方回退规则模板。
    发送前的最终防线仍是既有的 ``quality_check`` + ``resolve`` 门禁。
    """
    draft = content.reply_draft or ""
    exp = case["expected"]
    order_id = exp.get("order_id")
    logistics = _state.logistics_by_order.get(order_id, {}) if order_id else {}

    # 1. 不得命中已有 FORBIDDEN_RE 中的未经授权承诺（退款金额/时效、补发已执行、
    #    保证送达时间、赔偿金额等）。
    for rx in FORBIDDEN_RE:
        if rx.search(draft):
            return False, "命中未经授权承诺（退款金额/时效、补发已执行、保证送达时间等）"

    # 2. 有订单号时，草稿中出现的任何 CF 订单号必须与案例订单号一致。
    mentioned = ORDER_PATTERN.findall(draft)
    if order_id:
        if any(o != order_id for o in mentioned):
            return False, f"草稿出现与案例不一致的订单号（案例订单号为 {order_id}）"
    # 3. 无订单号时，草稿不得编造任何 CF 订单号。
    else:
        if mentioned:
            return False, "案例无订单号，草稿不得编造订单号"

    # 4. 物流并非 delivered 时，草稿不得声称已签收/已送达。
    if logistics and logistics.get("status") != "delivered":
        if DELIVERED_CLAIM_RE.search(draft):
            return False, "物流尚未签收，草稿不得声称已送达/已签收"

    # 5. 不得包含最终业务决策 / 自动退款 / 自动补发 / 自动结案语义。
    for rx in DECISION_SEMANTIC_RE:
        if rx.search(draft):
            return False, "包含最终业务决策或自动退款/补发/结案语义"

    return True, None


def llm_draft(case_id: str) -> dict:
    """生成 LLM 增强回复草稿（只读，不改变工单/会话/质检门禁状态）。

    仅增强英文回复草稿与信息提取；失败时自动回退到确定性规则模板
    ``_build_reply_draft()``。风险等级、命中规则、是否建单、是否审批、
    是否可结案等最终业务决策仍由确定性规则决定，本函数不参与。
    """
    case = _get_case(case_id)
    customer = _customer_of(case)
    exp = case["expected"]
    order_id = exp.get("order_id")
    order = _state.orders_by_id.get(order_id, {}) if order_id else {}
    logistics = _state.logistics_by_order.get(order_id, {}) if order_id else {}
    rules_summary = _rules_summary_of(case)

    fallback_reason: Optional[str] = None
    if settings.llm_configured():
        messages = prompts.build_draft_messages(
            customer_message=case["customer_message"],
            customer_alias=customer["alias"],
            country=customer["country"],
            language=customer["language"],
            order=order or None,
            logistics=logistics or None,
            rules_summary=rules_summary,
        )
        result = llm_client.chat_completion(messages)
        if result.success:
            data = _parse_llm_json(result.content)
            if isinstance(data, dict):
                # 越权字段优先判定：LLM 输出最终业务决策字段即视为不合规。
                forbidden = sorted(FORBIDDEN_DECISION_FIELDS & set(data.keys()))
                if forbidden:
                    fallback_reason = (
                        f"LLM 输出含禁用业务决策字段（{', '.join(forbidden)}），已回退规则模板"
                    )
                else:
                    try:
                        content = LLMDraftContent(**data)
                    except ValidationError:
                        content = None
                        fallback_reason = "LLM 返回内容不符合结构化模型，已回退规则模板"
                    if content is not None:
                        ok, reason = validate_llm_draft_safety(case, content)
                        if ok:
                            # 规范化英文缩写撇号相邻的异常空格（如 "I' m" -> "I'm"），
                            # 仅影响展示层空格，不改变订单号、金额、物流事实等业务内容。
                            content.reply_draft = normalize_apostrophe_spacing(
                                content.reply_draft
                            )
                            return LLMDraftResponse(
                                case_id=case_id,
                                source="llm",
                                fallback=False,
                                fallback_reason=None,
                                draft=content,
                            ).model_dump()
                        fallback_reason = f"LLM 草稿安全校验失败，已回退规则模板：{reason}"
            else:
                fallback_reason = "LLM 返回非法 JSON，已回退规则模板"
        else:
            fallback_reason = result.error
    else:
        fallback_reason = "LLM 未启用或未配置（LLM_ENABLED=false / 缺少 Key），已回退规则模板"

    # 回退：使用确定性规则模板生成草稿（与既有 Mock 行为一致）。
    fallback_draft = _build_reply_draft(case, customer)
    content = LLMDraftContent(
        intent=exp["intent"],
        sentiment=exp["sentiment"],
        extracted_order_id=order_id,
        missing_information=[] if order_id else ["full order number"],
        reply_draft=fallback_draft,
        disclaimer="本草稿为规则模板生成，仅供参考，需客服复核；不确认任何退款、补发或送达时间。",
    )
    return LLMDraftResponse(
        case_id=case_id,
        source="fallback_template",
        fallback=True,
        fallback_reason=fallback_reason,
        draft=content,
    ).model_dump()


def dashboard() -> dict:
    cases = _state.cases
    total = len(cases)
    if total == 0:
        return DashboardResponse(
            today_resolved=0,
            ai_adoption_rate=0,
            human_intervention_rate=0,
            reply_compliance_rate=100,
            issue_distribution=[],
            risk_distribution=[],
        ).model_dump()

    resolved = sum(1 for c in cases if _state.case_status[c["case_id"]] == "已解决")
    handoff = [c for c in cases if c["expected"]["handoff_required"]]
    ai_adoption = round((total - len(handoff)) / total * 100)
    human_rate = round(len(handoff) / total * 100)

    # Reply compliance: share of rule-generated drafts that pass the
    # content checks (facts / no unauthorized promise / next steps).
    compliant = 0
    for case in cases:
        draft = _build_reply_draft(case, _customer_of(case))
        lowered = draft.lower()
        ok = not any(rx.search(draft) for rx in FORBIDDEN_RE)
        ok = ok and any(k in lowered for k in NEXT_STEP_KEYWORDS)
        mentioned = ORDER_PATTERN.findall(draft)
        exp_order = case["expected"].get("order_id")
        if exp_order:
            ok = ok and all(o == exp_order for o in mentioned)
        else:
            ok = ok and len(mentioned) == 0
        if ok:
            compliant += 1
    compliance = round(compliant / total * 100)

    issue_counter: dict[str, int] = {}
    for case in cases:
        label = INTENT_ISSUE_TYPE.get(case["expected"]["intent"], "其他")
        issue_counter[label] = issue_counter.get(label, 0) + 1
    items = sorted(issue_counter.items(), key=lambda kv: kv[1], reverse=True)
    top = [DistributionItem(label=k, value=v) for k, v in items[:4]]
    rest = sum(v for _, v in items[4:])
    if rest:
        top.append(DistributionItem(label="其他", value=rest))

    risk_counter = {"高": 0, "中": 0, "低": 0}
    for case in cases:
        risk_counter[RISK_CN[case["expected"]["risk_level"]]] += 1

    return DashboardResponse(
        today_resolved=resolved,
        ai_adoption_rate=ai_adoption,
        human_intervention_rate=human_rate,
        reply_compliance_rate=compliance,
        issue_distribution=top,
        risk_distribution=[
            DistributionItem(label="高风险", value=risk_counter["高"]),
            DistributionItem(label="中风险", value=risk_counter["中"]),
            DistributionItem(label="低风险", value=risk_counter["低"]),
        ],
    ).model_dump()
