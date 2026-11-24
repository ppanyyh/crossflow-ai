"""CrossFlow AI V4 Mock API (FastAPI application entry point).

Run from the `backend` directory:

    python -m uvicorn main:app --reload --port 8000

Then open http://127.0.0.1:8000/ for the frontend workbench.

This service only reads simulated JSON data from `<project root>/data/`.
It is a portfolio prototype, NOT a production system.
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import service
from schemas import (
    ApproveRequest,
    DashboardResponse,
    QualityCheckRequest,
    QualityCheckResponse,
    ResolveRequest,
    Ticket,
    TicketCreateRequest,
)

app = FastAPI(
    title="CrossFlow AI V4 Mock API",
    version="0.1.0",
    description=(
        "跨境电商售后智能协同工作台 - Mock 后端。"
        "仅读取项目 data/ 目录的模拟数据，不接入真实平台与外部 API。"
        "本服务为作品集原型，非生产系统。"
    ),
)

# Allow the local HTML demo (file:// or other ports) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    """Liveness check (extra, not part of the contract)."""
    return {"status": "ok", "service": "crossflow-mock", "simulated": True}


@app.get("/api/cases")
def list_cases(
    status: Optional[str] = Query(default=None, description="按会话状态过滤"),
    risk_level: Optional[str] = Query(default=None, description="按风险等级过滤：低/中/高"),
) -> list[dict]:
    return service.list_case_summaries(status, risk_level)


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    try:
        return service.get_case_detail(case_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tickets")
def create_ticket(req: TicketCreateRequest) -> dict:
    try:
        return service.create_ticket(req.case_id, req.agent_note)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.BusinessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/tickets")
def list_tickets(
    status: Optional[str] = Query(default=None, description="按工单状态过滤")
) -> list[dict]:
    return service.list_tickets(status)


@app.post("/api/tickets/{ticket_id}/approve")
def approve_ticket(ticket_id: str, req: ApproveRequest) -> dict:
    try:
        return service.approve_ticket(ticket_id, req.decision, req.supervisor_note)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.BusinessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/cases/{case_id}/resolve")
def resolve_case(case_id: str, req: ResolveRequest) -> dict:
    try:
        return service.resolve_case(case_id, req.action, req.reply)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.BusinessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/quality-check")
def quality_check(req: QualityCheckRequest) -> dict:
    try:
        return service.quality_check(req.case_id, req.reply)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.BusinessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/cases/{case_id}/llm-draft")
def llm_draft(case_id: str) -> dict:
    """生成 LLM 增强回复草稿（只读，不改变工单或会话状态）。

    返回 `source=llm` 或 `source=fallback_template`，并说明是否回退及原因。
    """
    try:
        return service.llm_draft(case_id)
    except service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/dashboard")
def get_dashboard() -> dict:
    return service.dashboard()


# ---------------------------------------------------------------------------
# Static frontend (mounted LAST so /api/* routes keep priority)
# Directory: ../frontend relative to this file, independent of the CWD.
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
