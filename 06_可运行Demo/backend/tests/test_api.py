"""Interface tests for the CrossFlow AI V4 Mock backend.

Covers the six mandatory scenarios plus a few extra guard-rail cases:
1. 高风险案例创建工单
2. 未审批高风险案例不能 resolve
3. 低风险案例可 resolve
4. 订单号缺失案例进入待客户补充
5. 主管审批后状态变为处理中
6. 质检能够识别未经授权承诺
"""
import pytest
from fastapi.testclient import TestClient

import service
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


def _create_ticket(case_id: str, note: str = "请尽快审核") -> dict:
    resp = client.post("/api/tickets", json={"case_id": case_id, "agent_note": note})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve(ticket_id: str) -> dict:
    resp = client.post(
        f"/api/tickets/{ticket_id}/approve",
        json={
            "decision": "批准补发建议",
            "supervisor_note": "物流停滞超过12天，批准补发建议。",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. 高风险案例创建工单
# ---------------------------------------------------------------------------


def test_high_risk_case_creates_ticket():
    ticket = _create_ticket("CF-DEV-002", "客户已多次催促退款，建议优先审核。")
    assert ticket["case_id"] == "CF-DEV-002"
    assert ticket["risk_level"] == "高"
    assert ticket["status"] == "待人工审核"
    assert ticket["supervisor_decision"] is None
    assert len(ticket["audit_log"]) == 1
    assert "严重物流停滞" in ticket["reason"]

    # 会话状态同步为待人工审核
    detail = client.get("/api/cases/CF-DEV-002").json()
    assert detail["conversation_status"] == "待人工审核"
    assert detail["ticket"]["ticket_id"] == ticket["ticket_id"]


def test_duplicate_ticket_creation_is_idempotent():
    first = _create_ticket("CF-DEV-002")
    second = client.post(
        "/api/tickets", json={"case_id": "CF-DEV-002", "agent_note": "再次提交"}
    ).json()
    assert second["ticket_id"] == first["ticket_id"]
    assert len(client.get("/api/tickets").json()) == 1


def test_low_risk_case_cannot_create_ticket():
    resp = client.post("/api/tickets", json={"case_id": "CF-DEV-001"})
    assert resp.status_code == 400
    assert "不允许创建工单" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. 未审批高风险案例不能 resolve
# ---------------------------------------------------------------------------


def test_unapproved_high_risk_cannot_resolve():
    _create_ticket("CF-DEV-002")  # 工单已创建但未审批
    resp = client.post(
        "/api/cases/CF-DEV-002/resolve",
        json={"action": "send_reply", "reply": GOOD_REPLY},
    )
    assert resp.status_code == 403
    assert "未经主管审批" in resp.json()["detail"]

    detail = client.get("/api/cases/CF-DEV-002").json()
    assert detail["conversation_status"] == "待人工审核"


def test_high_risk_without_ticket_cannot_resolve():
    resp = client.post(
        "/api/cases/CF-DEV-008/resolve",
        json={"action": "send_reply", "reply": GOOD_REPLY},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. 低风险案例可 resolve
# ---------------------------------------------------------------------------


def test_low_risk_case_can_resolve():
    reply = (
        "Hi Liam, thank you for reaching out. Your order CF20260926002 is still in "
        "transit - the latest carrier update is 'Item received at export facility' "
        "(2026-09-22). We will keep monitoring the shipment and share any new updates."
    )
    resp = client.post(
        "/api/cases/CF-DEV-001/resolve", json={"action": "send_reply", "reply": reply}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation_status"] == "已解决"

    summaries = client.get("/api/cases", params={"status": "已解决"}).json()
    assert [s["case_id"] for s in summaries] == ["CF-DEV-001"]


# ---------------------------------------------------------------------------
# 4. 订单号缺失案例进入待客户补充
# ---------------------------------------------------------------------------


def test_missing_order_number_goes_to_pending_customer_info():
    resp = client.post(
        "/api/cases/CF-DEV-004/resolve",
        json={
            "action": "request_order_information",
            "reply": "Could you please provide your full order number?",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation_status"] == "待客户补充"

    detail = client.get("/api/cases/CF-DEV-004").json()
    assert detail["ai_decision"]["recommended_action"] == "request_order_information"
    assert detail["order"] == {}


def test_invalid_order_number_case_also_pending_info():
    resp = client.post(
        "/api/cases/CF-DEV-011/resolve",
        json={"action": "request_order_information", "reply": "请提供完整订单号"},
    )
    assert resp.status_code == 200
    assert resp.json()["conversation_status"] == "待客户补充"


def test_case_with_order_cannot_request_order_information():
    resp = client.post(
        "/api/cases/CF-DEV-001/resolve",
        json={"action": "request_order_information", "reply": "请提供订单号"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. 主管审批后状态变为处理中
# ---------------------------------------------------------------------------


def test_supervisor_approval_moves_ticket_and_case_to_processing():
    ticket = _create_ticket("CF-DEV-002")
    approved = _approve(ticket["ticket_id"])

    assert approved["status"] == "处理中"
    assert approved["supervisor_decision"] == "批准补发建议"
    assert "物流停滞超过12天" in approved["supervisor_note"]
    assert len(approved["audit_log"]) == 2  # 创建 + 审批

    detail = client.get("/api/cases/CF-DEV-002").json()
    assert detail["conversation_status"] == "处理中"
    assert detail["ticket"]["status"] == "处理中"

    # 审批后高风险案例可 resolve（须先通过 AI 质检，门禁见下方专属用例）
    checked = client.post(
        "/api/quality-check", json={"case_id": "CF-DEV-002", "reply": GOOD_REPLY}
    )
    assert checked.json()["overall_passed"] is True

    resolved = client.post(
        "/api/cases/CF-DEV-002/resolve", json={"action": "send_reply", "reply": GOOD_REPLY}
    )
    assert resolved.status_code == 200
    assert resolved.json()["conversation_status"] == "已解决"
    closed = client.get("/api/tickets", params={"status": "已关闭"}).json()
    assert [t["ticket_id"] for t in closed] == [ticket["ticket_id"]]


def test_cannot_approve_twice():
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])
    resp = client.post(
        f"/api/tickets/{ticket['ticket_id']}/approve",
        json={"decision": "驳回并改写回复", "supervisor_note": "二次审批"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 6. 质检能够识别未经授权承诺
# ---------------------------------------------------------------------------


def _check(case_id: str, reply: str) -> dict:
    resp = client.post("/api/quality-check", json={"case_id": case_id, "reply": reply})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_quality_check_detects_unauthorized_promise():
    # 先完成高风险审批，隔离出“未经授权承诺”这一失败项
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])

    result = _check("CF-DEV-002", BAD_REPLY)
    by_name = {c["name"]: c["passed"] for c in result["checks"]}
    assert by_name["无未经授权承诺"] is False
    assert by_name["高风险已完成主管审核"] is True
    assert result["overall_passed"] is False


def test_quality_check_passes_compliant_reply():
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])

    result = _check("CF-DEV-002", GOOD_REPLY)
    assert result["overall_passed"] is True
    assert all(c["passed"] for c in result["checks"])


def test_quality_check_flags_unapproved_high_risk():
    _create_ticket("CF-DEV-002")  # 未审批
    result = _check("CF-DEV-002", GOOD_REPLY)
    by_name = {c["name"]: c["passed"] for c in result["checks"]}
    assert by_name["高风险已完成主管审核"] is False
    assert result["overall_passed"] is False


def test_quality_check_detects_wrong_order_number():
    reply = (
        "Hi Emma, your order CF20260926099 is under review, we will check the "
        "logistics record and get back to you."
    )
    result = _check("CF-DEV-002", reply)
    by_name = {c["name"]: c["passed"] for c in result["checks"]}
    assert by_name["订单与物流事实一致"] is False


# ---------------------------------------------------------------------------
# 6b. 质检门禁（V2.1）：高风险 / handoff_required 案例须质检通过才可 resolve
# ---------------------------------------------------------------------------


def test_high_risk_resolve_requires_quality_pass():
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])

    # 已审批但未质检，直接 resolve 应被 403 拦截，且给出中文质检门禁错误
    resp = client.post(
        "/api/cases/CF-DEV-002/resolve", json={"action": "send_reply", "reply": GOOD_REPLY}
    )
    assert resp.status_code == 403
    assert "质检" in resp.json()["detail"]

    detail = client.get("/api/cases/CF-DEV-002").json()
    assert detail["conversation_status"] == "处理中"
    assert detail["quality_passed"] is False


def test_quality_check_gates_resolve_until_passed():
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])

    # 质检未通过（含未授权承诺）不能 resolve
    failed = _check("CF-DEV-002", BAD_REPLY)
    assert failed["overall_passed"] is False
    resp = client.post(
        "/api/cases/CF-DEV-002/resolve", json={"action": "send_reply", "reply": BAD_REPLY}
    )
    assert resp.status_code == 403

    # 质检通过后可 resolve
    passed = _check("CF-DEV-002", GOOD_REPLY)
    assert passed["overall_passed"] is True
    ok = client.post(
        "/api/cases/CF-DEV-002/resolve", json={"action": "send_reply", "reply": GOOD_REPLY}
    )
    assert ok.status_code == 200
    assert ok.json()["conversation_status"] == "已解决"


def test_quality_passed_resets_after_failed_recheck():
    ticket = _create_ticket("CF-DEV-002")
    _approve(ticket["ticket_id"])

    # 质检通过 -> 标记写入
    _check("CF-DEV-002", GOOD_REPLY)
    assert client.get("/api/cases/CF-DEV-002").json()["quality_passed"] is True

    # 修改回复后重新质检未通过 -> 旧标记被清除，不能 resolve
    _check("CF-DEV-002", BAD_REPLY)
    assert client.get("/api/cases/CF-DEV-002").json()["quality_passed"] is False
    resp = client.post(
        "/api/cases/CF-DEV-002/resolve", json={"action": "send_reply", "reply": GOOD_REPLY}
    )
    assert resp.status_code == 403


def test_low_risk_and_missing_order_ignore_quality_gate():
    # 低风险直接结案，不要求质检门禁
    resp = client.post(
        "/api/cases/CF-DEV-001/resolve", json={"action": "send_reply", "reply": GOOD_REPLY}
    )
    assert resp.status_code == 200
    assert resp.json()["conversation_status"] == "已解决"

    # 订单号缺失走「待客户补充」，同样不要求质检门禁
    resp2 = client.post(
        "/api/cases/CF-DEV-004/resolve",
        json={"action": "request_order_information", "reply": "please provide order number"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["conversation_status"] == "待客户补充"


# ---------------------------------------------------------------------------
# 其他守卫用例
# ---------------------------------------------------------------------------


def test_case_not_found_returns_404():
    assert client.get("/api/cases/CF-DEV-999").status_code == 404
    assert client.post("/api/tickets", json={"case_id": "CF-DEV-999"}).status_code == 404
    assert client.post("/api/quality-check", json={"case_id": "X", "reply": "hi"}).status_code == 404


def test_resolve_requires_reply_content():
    resp = client.post(
        "/api/cases/CF-DEV-001/resolve", json={"action": "send_reply", "reply": "  "}
    )
    assert resp.status_code == 422


def test_invalid_approve_decision_rejected():
    ticket = _create_ticket("CF-DEV-002")
    resp = client.post(
        f"/api/tickets/{ticket['ticket_id']}/approve",
        json={"decision": "直接退款", "supervisor_note": "x"},
    )
    assert resp.status_code == 422  # Pydantic Literal 校验


def test_cases_filters():
    high = client.get("/api/cases", params={"risk_level": "高"}).json()
    assert high and all(s["risk_level"] == "高" for s in high)
    low = client.get("/api/cases", params={"risk_level": "低"}).json()
    assert low and all(s["risk_level"] == "低" for s in low)


def test_dashboard_shape_and_simulated_flag():
    data = client.get("/api/dashboard").json()
    assert data["simulated"] is True
    assert data["today_resolved"] == 0
    assert data["ai_adoption_rate"] + data["human_intervention_rate"] <= 101
    assert {d["label"] for d in data["risk_distribution"]} == {"高风险", "中风险", "低风险"}
    assert all(d["value"] >= 0 for d in data["issue_distribution"])


def test_case_detail_contract_shape():
    detail = client.get("/api/cases/CF-DEV-002").json()
    assert detail["customer"]["alias"] == "Emma R."
    assert detail["order"]["order_id"] == "CF20260926001"
    assert detail["logistics"]["carrier"] == "USPS"
    assert detail["ai_decision"]["risk_level"] == "高"
    assert detail["ai_decision"]["applied_rule_ids"] == ["L-03", "L-06"]
    assert detail["ai_decision"]["handoff_required"] is True
    assert detail["ticket"] is None
    # 回复草稿不得包含未授权承诺
    draft = detail["ai_decision"]["reply_draft"]
    assert "review" in draft.lower()
    assert "$" not in draft and "guarantee" not in draft.lower()
