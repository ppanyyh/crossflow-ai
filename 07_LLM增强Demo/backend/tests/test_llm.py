"""LLM 增强草稿接口测试（V7 阶段一）。

覆盖 5 个必备场景：
1. LLM 未启用时自动回退规则模板；
2. LLM 成功返回合规结构化草稿；
3. LLM 超时回退；
4. LLM 返回非法 JSON 回退；
5. LLM 草稿含越权承诺时质检失败且不能结案。

这些测试通过 mock `settings.llm_configured` 与 `llm_client.chat_completion` 来
模拟 LLM 的各种结果，全程不接入真实 LLM、不读取任何真实 API Key。
"""
import json

import pytest
from fastapi.testclient import TestClient

import llm_client
import service
import settings
from main import app

client = TestClient(app)

GOOD_REPLY = (
    "Hi Emma, we are truly sorry for the delay with your order. Your case is under "
    "review by our specialist team, and we will check the full logistics record and "
    "get back to you with the result. We cannot confirm any outcome before the "
    "review is complete."
)

BAD_REPLY = (
    "Hi Emma, we are so sorry. We will refund you $49.99 within 3 business days and "
    "a replacement has been sent. It will arrive before Friday."
)


@pytest.fixture(autouse=True)
def reset_state():
    service.state.reset()
    yield
    service.state.reset()


def _llm_json(draft: str) -> str:
    return json.dumps(
        {
            "intent": "tracking_stalled_refund_request",
            "sentiment": "negative",
            "extracted_order_id": "CF20260926001",
            "missing_information": [],
            "reply_draft": draft,
            "disclaimer": "draft requires human review",
        }
    )


def _mock_llm(monkeypatch, result: llm_client.LLMCallResult):
    monkeypatch.setattr(settings, "llm_configured", lambda: True)
    monkeypatch.setattr(llm_client, "chat_completion", lambda messages: result)


# ---------------------------------------------------------------------------
# 1. LLM 未启用时自动回退
# ---------------------------------------------------------------------------


def test_llm_disabled_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(settings, "llm_configured", lambda: False)

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "LLM 未启用" in r["fallback_reason"]
    assert r["draft"]["reply_draft"]
    # 规则模板草稿不含越权承诺
    assert "$" not in r["draft"]["reply_draft"]
    assert "guarantee" not in r["draft"]["reply_draft"].lower()


# ---------------------------------------------------------------------------
# 2. LLM 成功返回合规结构化草稿
# ---------------------------------------------------------------------------


def test_llm_success_returns_compliant_draft(monkeypatch):
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(GOOD_REPLY)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "llm"
    assert r["fallback"] is False
    assert r["fallback_reason"] is None
    assert r["draft"]["reply_draft"] == GOOD_REPLY
    assert r["draft"]["extracted_order_id"] == "CF20260926001"
    assert r["draft"]["intent"] == "tracking_stalled_refund_request"


# ---------------------------------------------------------------------------
# 3. LLM 超时回退
# ---------------------------------------------------------------------------


def test_llm_timeout_falls_back(monkeypatch):
    _mock_llm(
        monkeypatch,
        llm_client.LLMCallResult(False, error="LLM 调用超时，已回退规则模板"),
    )

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "超时" in r["fallback_reason"]
    assert r["draft"]["reply_draft"]


# ---------------------------------------------------------------------------
# 4. LLM 返回非法 JSON 回退
# ---------------------------------------------------------------------------


def test_llm_invalid_json_falls_back(monkeypatch):
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content="this is not json {"))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "非法 JSON" in r["fallback_reason"]


# ---------------------------------------------------------------------------
# 5. LLM 草稿含越权承诺时，质检失败且不能结案
# ---------------------------------------------------------------------------


def test_llm_unauthorized_promise_falls_back_and_final_gate_still_enforced(monkeypatch):
    """越权草稿在返回前即降级为规则模板；即便绕过 llm-draft 直接提交越权回复，
    既有 quality-check 与 resolve 门禁仍作为发送前的最终防线阻止结案。"""
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(BAD_REPLY)))

    # 阶段 1.1 新行为：含退款金额 + 补发已执行的草稿在返回前即被降级，而非原样返回
    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "安全校验失败" in r["fallback_reason"]
    assert "$" not in r["draft"]["reply_draft"]

    # 最终防线：建单 + 审批后，直接向 quality-check 提交越权回复仍被拦截
    t = client.post("/api/tickets", json={"case_id": "CF-DEV-002", "agent_note": "x"}).json()
    client.post(
        f"/api/tickets/{t['ticket_id']}/approve",
        json={"decision": "批准补发建议", "supervisor_note": "x"},
    )

    # 越权回复绕过 llm-draft 直接送检 → 质检失败
    qc = client.post(
        "/api/quality-check",
        json={"case_id": "CF-DEV-002", "reply": BAD_REPLY},
    ).json()
    by_name = {c["name"]: c["passed"] for c in qc["checks"]}
    assert by_name["无未经授权承诺"] is False
    assert qc["overall_passed"] is False

    # 质检未通过 → 不能结案（403）
    resp = client.post(
        "/api/cases/CF-DEV-002/resolve",
        json={"action": "send_reply", "reply": BAD_REPLY},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 阶段 1.1：LLM 输出安全加固（返回前预校验自动降级，不依赖人工质检）
# ---------------------------------------------------------------------------


def test_llm_forbidden_decision_fields_fall_back(monkeypatch):
    """LLM 输出包含 risk_level / approval 等禁用业务决策字段时自动回退。"""
    payload = json.loads(_llm_json(GOOD_REPLY))
    payload["risk_level"] = "高"  # 越权业务决策字段
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=json.dumps(payload)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "禁用业务决策字段" in r["fallback_reason"]
    assert "risk_level" in r["fallback_reason"]


def test_llm_refund_reship_promise_falls_back(monkeypatch):
    """LLM 草稿承诺退款金额/退款时效/补发已执行时，返回前即自动回退。"""
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(BAD_REPLY)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "安全校验失败" in r["fallback_reason"]
    assert "$" not in r["draft"]["reply_draft"]


def test_llm_wrong_order_number_falls_back(monkeypatch):
    """LLM 草稿含与案例不一致的订单号时自动回退。"""
    wrong = (
        "Hi Emma, we are checking the status of your order CF20260926999 and will "
        "get back to you soon."
    )
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(wrong)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "订单号" in r["fallback_reason"]


def test_llm_fabricated_delivered_falls_back(monkeypatch):
    """LLM 草稿虚构已签收/已送达而物流事实不支持时自动回退。"""
    delivered = (
        "Hi Emma, your order CF20260926001 has been delivered. Please check your mailbox."
    )
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(delivered)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert ("签收" in r["fallback_reason"] or "送达" in r["fallback_reason"])


def test_llm_final_decision_semantics_falls_back(monkeypatch):
    """LLM 草稿包含最终业务决策/自动结案语义时自动回退。"""
    decided = (
        "Hi Emma, we have reviewed your case. Your case has been resolved and closed. "
        "No further action is needed."
    )
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(decided)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "fallback_template"
    assert r["fallback"] is True
    assert "安全校验失败" in r["fallback_reason"]


# ---------------------------------------------------------------------------
# V1.6：英文草稿撇号相邻异常空格规范化
# ---------------------------------------------------------------------------


def test_normalize_apostrophe_spacing_unit():
    """仅移除英文缩写撇号相邻空格，不触碰订单号/金额/物流事实。"""
    assert service.normalize_apostrophe_spacing("I' m so sorry.") == "I'm so sorry."
    assert service.normalize_apostrophe_spacing("it hasn' t arrived yet.") == "it hasn't arrived yet."
    assert service.normalize_apostrophe_spacing("we' ll keep monitoring.") == "we'll keep monitoring."
    assert service.normalize_apostrophe_spacing("I 'm sorry") == "I'm sorry"
    # Unicode 右单引号同样处理
    assert service.normalize_apostrophe_spacing("I\u2019 m sorry") == "I\u2019m sorry"
    # 不修改订单号、金额、物流事实
    assert (
        service.normalize_apostrophe_spacing("order CF20260926001 is still in transit")
        == "order CF20260926001 is still in transit"
    )
    assert service.normalize_apostrophe_spacing("amount $49.99 unchanged") == "amount $49.99 unchanged"
    # 空串与无撇号文本原样返回
    assert service.normalize_apostrophe_spacing("") == ""
    assert service.normalize_apostrophe_spacing("hello world") == "hello world"


def test_llm_draft_normalizes_apostrophe_spacing(monkeypatch):
    """LLM 草稿含英文缩写撇号异常空格时，返回前自动规范化为紧邻形式。"""
    raw = (
        "Hi Emma, I' m sorry for the delay. We haven' t received a new update yet, "
        "but your order CF20260926001 is still in transit and we' ll keep monitoring it."
    )
    _mock_llm(monkeypatch, llm_client.LLMCallResult(True, content=_llm_json(raw)))

    r = client.post("/api/cases/CF-DEV-002/llm-draft").json()
    assert r["source"] == "llm"
    assert r["fallback"] is False
    draft = r["draft"]["reply_draft"]
    assert "I' m" not in draft
    assert "n' t" not in draft
    assert "we' ll" not in draft
    assert "I'm sorry" in draft
    assert "haven't" in draft
    assert "we'll" in draft
    # 订单号未被规范化破坏
    assert "CF20260926001" in draft
