"""Pydantic v2 schemas for the CrossFlow AI V4 Mock API.

All response/request models follow `docs/API_CONTRACT.md`.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enum-like literals (contract section 2)
# ---------------------------------------------------------------------------

RiskLevel = Literal["低", "中", "高"]
CaseStatus = Literal["待处理", "待客户补充", "待人工审核", "处理中", "已解决", "已关闭"]
TicketStatus = Literal["待人工审核", "处理中", "已关闭"]

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CaseSummary(BaseModel):
    case_id: str
    customer_alias: str
    country: str
    issue_type: str
    order_id: Optional[str] = None
    risk_level: RiskLevel
    conversation_status: CaseStatus
    updated_at: str


class CustomerInfo(BaseModel):
    alias: str
    country: str
    language: str


class AIDecision(BaseModel):
    intent: str
    sentiment: str
    risk_level: RiskLevel
    applied_rule_ids: list[str] = Field(default_factory=list)
    applied_rule_names: list[str] = Field(default_factory=list)
    recommended_action: str
    handoff_required: bool
    reply_draft: str


class AuditLogEntry(BaseModel):
    time: str
    actor: str
    action: str
    detail: str


class Ticket(BaseModel):
    ticket_id: str
    case_id: str
    reason: str
    risk_level: RiskLevel
    status: TicketStatus
    agent_note: Optional[str] = None
    supervisor_decision: Optional[str] = None
    supervisor_note: Optional[str] = None
    audit_log: list[AuditLogEntry] = Field(default_factory=list)


class CaseDetail(BaseModel):
    case_id: str
    customer_message: str
    customer: CustomerInfo
    order: dict[str, Any] = Field(default_factory=dict)
    logistics: dict[str, Any] = Field(default_factory=dict)
    ai_decision: AIDecision
    conversation_status: CaseStatus
    ticket: Optional[Ticket] = None
    # 质检通过门禁标记：仅当最近一次 quality-check 返回 overall_passed=true 时为 True。
    # 中/高风险（或 handoff_required）案例在 resolve 前必须满足该标记。
    quality_passed: bool = False


class QualityCheckItem(BaseModel):
    name: str
    passed: bool


class QualityCheckResponse(BaseModel):
    case_id: str
    checks: list[QualityCheckItem]
    overall_passed: bool


class DistributionItem(BaseModel):
    label: str
    value: int


class DashboardResponse(BaseModel):
    today_resolved: int
    ai_adoption_rate: int
    human_intervention_rate: int
    reply_compliance_rate: int
    issue_distribution: list[DistributionItem]
    risk_distribution: list[DistributionItem]
    simulated: bool = True


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TicketCreateRequest(BaseModel):
    case_id: str
    agent_note: Optional[str] = None


class ApproveRequest(BaseModel):
    decision: Literal["要求补充材料", "批准补发建议", "批准退款建议", "驳回并改写回复"]
    supervisor_note: Optional[str] = None


class ResolveRequest(BaseModel):
    action: Literal["send_reply", "request_order_information"]
    reply: Optional[str] = None


class QualityCheckRequest(BaseModel):
    case_id: str
    reply: str


# ---------------------------------------------------------------------------
# LLM 增强（V7 阶段一）：结构化草稿模型
# ---------------------------------------------------------------------------
# 注意：LLM 输出不得包含 risk_level / handoff_required / approval / resolve
# 等最终业务决策字段——这些仍由 service.py 的确定性规则决定。


class LLMDraftContent(BaseModel):
    """LLM 生成（或规则模板降级）的回复草稿结构化内容。

    - ``extra="forbid"``：拒绝任何额外字段（如 ``risk_level`` /
      ``handoff_required`` / ``approval`` / ``resolve`` / ``ticket`` /
      ``refund_decision`` 等最终业务决策字段），而不是静默忽略。
    - 对 ``reply_draft`` / ``intent`` / ``disclaimer`` 施加长度限制，
      避免空值或异常超长文本。
    """

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(default="", min_length=1, max_length=100)
    sentiment: str = Field(default="", max_length=50)
    extracted_order_id: Optional[str] = Field(default=None, max_length=64)
    missing_information: list[str] = Field(default_factory=list)
    reply_draft: str = Field(default="", min_length=1, max_length=4000)
    disclaimer: str = Field(default="", min_length=1, max_length=500)


class LLMDraftResponse(BaseModel):
    """POST /api/cases/{case_id}/llm-draft 的响应。

    仅返回草稿生成结果与来源，不改变任何工单或会话状态。
    """

    case_id: str
    source: Literal["llm", "fallback_template"]
    fallback: bool
    fallback_reason: Optional[str] = None
    draft: LLMDraftContent
