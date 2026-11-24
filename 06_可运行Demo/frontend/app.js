/* ============================================================
   CrossFlow AI V4 · 前端逻辑 V2（原生 JS，无框架无构建）
   页面信息架构对齐 04_页面信息架构与状态机.md / Axure_制作规格_V4.md
   所有业务数据均通过 fetch 调用 FastAPI，前端不硬编码业务结果。
   ============================================================ */

const API = ""; // 同源：由后端静态挂载于 http://127.0.0.1:8000/

/* ---------------- 全局状态 ---------------- */
let currentCaseId = null;      // 对应 Axure 变量 currentCaseId
let currentDetail = null;      // 当前会话详情（CaseDetail）
let currentTicketId = null;    // 主管审批页当前工单
let caseFilter = { status: "", risk: "" }; // 会话列表筛选
let qcDraftCarry = "";         // 从详情页带入质检页的草稿

const RISK_COLOR = { "高": "var(--danger)", "中": "var(--warning)", "低": "var(--success)" };
const DONUT_COLORS = ["#2563EB", "#D97706", "#16A34A", "#DC2626", "#7C3AED", "#0891B2", "#DB2777", "#65A30D"];

/* ---------------- 基础工具 ---------------- */

async function api(path, options = {}) {
  const resp = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try { data = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : resp.status;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function riskBadge(level) {
  const map = { "高": "high", "中": "medium", "低": "low" };
  return `<span class="badge badge-risk-${map[level] || "low"}">${esc(level)}风险</span>`;
}

function statusBadge(status) {
  const cls = status === "已解决" || status === "已关闭" ? "resolved"
    : status === "处理中" ? "processing"
    : status === "待客户补充" ? "warn"
    : status === "待处理" ? "pending" : "";
  return `<span class="badge badge-status ${cls}">${esc(status)}</span>`;
}

/* ---------------- 视图切换 ---------------- */

const VIEW_LOADERS = {
  home: () => loadHome(),
  cases: () => loadCases(),
  tickets: () => loadTickets(),
  quality: () => loadQuality(),
  dashboard: () => loadDashboard(),
};

function showView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.querySelectorAll(".nav-item").forEach(b =>
    b.classList.toggle("active", b.dataset.view === name));
  const view = document.getElementById("view-" + name);
  if (view) view.classList.remove("hidden");
  if (VIEW_LOADERS[name]) VIEW_LOADERS[name]();
}

document.querySelectorAll(".nav-item").forEach(btn =>
  btn.addEventListener("click", () => showView(btn.dataset.view)));

function goBackToCases() { showView("cases"); }

/* ---------------- API 健康状态 ---------------- */

async function checkApi() {
  const el = document.getElementById("apiStatus");
  try {
    await api("/api/health");
    el.textContent = "API 已连接";
    el.className = "api-dot ok";
  } catch (_) {
    el.textContent = "API 未连接";
    el.className = "api-dot bad";
  }
}

/* ============================================================
   P1 工作台首页
   ============================================================ */

async function loadHome() {
  try {
    const [cases, dash] = await Promise.all([api("/api/cases"), api("/api/dashboard")]);
    const pending = cases.filter(c => c.conversation_status === "待处理").length;
    const reviewing = cases.filter(c => c.conversation_status === "待人工审核").length;
    const open = cases.filter(c => c.risk_level === "高" && !["已解决", "已关闭"].includes(c.conversation_status));
    const resolved = dash.today_resolved;

    document.getElementById("homeStats").innerHTML = `
      <div class="stat-card c-blue" onclick="filterAndGo('','')">
        <div class="stat-label">待处理</div><div class="stat-value">${pending}</div>
        <div class="stat-hint">点击查看待处理会话</div></div>
      <div class="stat-card c-blue" onclick="filterAndGo('待人工审核','')">
        <div class="stat-label">待人工审核</div><div class="stat-value">${reviewing}</div>
        <div class="stat-hint">点击查看待审核会话</div></div>
      <div class="stat-card c-red" onclick="filterAndGo('','高')">
        <div class="stat-label">高风险会话</div><div class="stat-value">${open.length}</div>
        <div class="stat-hint">点击筛选高风险</div></div>
      <div class="stat-card c-green" onclick="showView('dashboard')">
        <div class="stat-label">今日已解决</div><div class="stat-value">${resolved}</div>
        <div class="stat-hint">点击查看运营看板</div></div>`;

    document.getElementById("homeHighCount").textContent = open.length;
    document.getElementById("homeHighRisk").innerHTML = open.length ? open.map(c => `
      <div class="todo-item" onclick="openDetail('${esc(c.case_id)}')">
        <div class="todo-main">
          <div class="todo-title">${esc(c.customer_alias)} · ${esc(c.country)}</div>
          <div class="todo-desc">${esc(c.issue_type)} · ${esc(c.order_id || "订单号缺失")}</div>
        </div>
        <div class="todo-meta">${riskBadge(c.risk_level)}<br>${statusBadge(c.conversation_status)}</div>
      </div>`).join("")
      : `<p class="muted">当前没有待处理的高风险会话 🎉</p>`;

    document.getElementById("homeEfficiency").innerHTML = `
      <div class="kv-big"><span class="kv-big-label">AI 建议采纳率</span>
        <span class="kv-big-value">${dash.ai_adoption_rate}%</span></div>
      <div class="kv-big"><span class="kv-big-label">人工介入率</span>
        <span class="kv-big-value">${dash.human_intervention_rate}%</span></div>
      <div class="kv-big"><span class="kv-big-label">回复合规率</span>
        <span class="kv-big-value">${dash.reply_compliance_rate}%</span></div>`;
  } catch (e) { toast("工作台加载失败：" + e.message, true); }
}

function filterAndGo(status, risk) {
  caseFilter = { status, risk };
  showView("cases");
}

/* ============================================================
   P2 会话列表
   ============================================================ */

function renderFilterButtons() {
  const statuses = [
    ["", "全部"], ["待处理", "待处理"], ["待人工审核", "待人工审核"],
    ["待客户补充", "待客户补充"], ["处理中", "处理中"], ["已解决", "已解决"],
  ];
  const risks = [["", "全部"], ["高", "高风险"], ["中", "中风险"], ["低", "低风险"]];
  document.getElementById("statusFilters").innerHTML = statuses.map(([v, l]) =>
    `<button class="filter-item ${caseFilter.status === v ? "active" : ""}" data-status="${v}"
      onclick="setStatusFilter('${v}')">${l}</button>`).join("");
  document.getElementById("riskFilters").innerHTML = risks.map(([v, l]) =>
    `<button class="filter-item ${caseFilter.risk === v ? "active" : ""}" data-risk="${v}"
      onclick="setRiskFilter('${v}')">${l}</button>`).join("");
}

function setStatusFilter(v) { caseFilter.status = v; renderFilterButtons(); loadCases(); }
function setRiskFilter(v) { caseFilter.risk = v; renderFilterButtons(); loadCases(); }

let allCasesCache = [];

async function loadCases() {
  renderFilterButtons();
  try {
    const params = new URLSearchParams();
    if (caseFilter.status) params.set("status", caseFilter.status);
    if (caseFilter.risk) params.set("risk_level", caseFilter.risk);
    let cases = await api("/api/cases?" + params.toString());
    allCasesCache = cases;

    // 国家 / 问题类型为前端辅助筛选（后端契约仅支持 status / risk_level）
    const country = document.getElementById("filterCountry").value;
    const issue = document.getElementById("filterIssue").value;
    if (country) cases = cases.filter(c => c.country === country);
    if (issue) cases = cases.filter(c => c.issue_type === issue);
    renderCaseOptions(cases);

    document.getElementById("caseTableBody").innerHTML = cases.length ? cases.map(c => `
      <tr onclick="openDetail('${esc(c.case_id)}')">
        <td><b>${esc(c.customer_alias)}</b></td>
        <td>${esc(c.country)}</td>
        <td class="cell-msg">${esc(c.issue_type)} — ${esc(c.order_id || "未提供订单号")}</td>
        <td>${riskBadge(c.risk_level)}</td>
        <td>${statusBadge(c.conversation_status)}</td>
        <td class="cell-case">${esc(c.case_id)}</td>
        <td><button class="btn btn-primary btn-sm" onclick="event.stopPropagation();openDetail('${esc(c.case_id)}')">详情</button></td>
      </tr>`).join("")
      : `<tr><td colspan="7" class="muted" style="text-align:center;padding:28px">无匹配会话</td></tr>`;
  } catch (e) { toast("会话列表加载失败：" + e.message, true); }
}

function renderCaseOptions(cases) {
  const countries = [...new Set(allCasesCache.map(c => c.country))].sort();
  const issues = [...new Set(allCasesCache.map(c => c.issue_type))].sort();
  const selC = document.getElementById("filterCountry");
  const selI = document.getElementById("filterIssue");
  const cVal = selC.value, iVal = selI.value;
  selC.innerHTML = `<option value="">全部国家</option>` +
    countries.map(c => `<option>${esc(c)}</option>`).join("");
  selI.innerHTML = `<option value="">全部问题类型</option>` +
    issues.map(i => `<option>${esc(i)}</option>`).join("");
  selC.value = cVal; selI.value = iVal;
}

/* ============================================================
   P3 会话详情（三栏）
   ============================================================ */

async function openDetail(caseId) {
  currentCaseId = caseId;
  showView("detail");
  document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
  document.getElementById("detailCaseId").textContent = "#" + caseId;
  document.getElementById("detailContent").classList.add("hidden");
  document.getElementById("detailNotFound").classList.add("hidden");
  try {
    currentDetail = await api("/api/cases/" + encodeURIComponent(caseId));
    renderDetail(currentDetail);
  } catch (e) {
    document.getElementById("detailNotFound").classList.remove("hidden");
    toast("加载失败：" + e.message, true);
  }
}

function renderDetail(d) {
  document.getElementById("detailContent").classList.remove("hidden");
  document.getElementById("detailIssueType").textContent =
    `${d.customer.alias} · ${d.customer.country} · ${d.ai_decision.intent}`;

  /* ---- 左栏：客户信息 ---- */
  document.getElementById("dCustomer").innerHTML = `
    <dt>匿名标识</dt><dd>${esc(d.customer.alias)}</dd>
    <dt>目的国</dt><dd>${esc(d.customer.country)}</dd>
    <dt>语言</dt><dd>${esc(d.customer.language)}</dd>`;

  /* ---- 左栏：会话状态 ---- */
  const t = d.ticket;
  document.getElementById("dStatus").innerHTML = `
    <div class="decision-row">${statusBadge(d.conversation_status)} ${riskBadge(d.ai_decision.risk_level)}</div>
    ${t ? `<div class="decision-kv"><b>关联工单</b>${esc(t.ticket_id)}（${esc(t.status)}）</div>` : ""}
    ${d.ai_decision.handoff_required
      ? `<div class="decision-kv"><b>处理要求</b>必须人工审核</div>`
      : `<div class="decision-kv"><b>处理要求</b>AI 可直接回复</div>`}
    ${d.ai_decision.handoff_required
      ? `<div class="decision-kv"><b>质检状态</b>${d.quality_passed ? "✅ 已通过（可结案）" : "未通过 / 未质检"}</div>`
      : ""}`;

  /* ---- 左栏：时间线 ---- */
  document.getElementById("dTimeline").innerHTML = buildTimeline(d);

  /* ---- 中栏：客户消息 ---- */
  document.getElementById("dCustomerMessage").textContent = d.customer_message;

  /* ---- 中栏：AI 分析 ---- */
  const ai = d.ai_decision;
  const matchedOrder = d.order && d.order.order_id ? d.order.order_id : null;
  document.getElementById("dAnalysis").innerHTML = `
    <div class="decision-row">${riskBadge(ai.risk_level)}
      <span class="muted">意图：${esc(ai.intent)}</span>
      <span class="muted">情绪：${esc(ai.sentiment)}</span></div>
    <div class="decision-kv"><b>已提取</b>订单号 ${matchedOrder ? esc(matchedOrder) : "（缺失）"} · 问题类型 ${esc(ai.intent)}</div>
    <div class="decision-kv"><b>缺失信息</b>${matchedOrder ? "无" : "完整订单号"}</div>
    <div class="decision-kv"><b>命中规则</b></div>
    ${ai.applied_rule_names.length
      ? ai.applied_rule_names.map((n, i) => `<span class="rule-chip">${esc(ai.applied_rule_ids[i])} · ${esc(n)}</span>`).join("")
      : '<span class="muted">无命中规则</span>'}`;

  /* ---- 中栏：回复草稿 ---- */
  document.getElementById("replyDraft").value = ai.reply_draft;
  renderReplyActions(d);

  /* ---- 右栏：订单物流事实 ---- */
  document.getElementById("dOrderFacts").innerHTML = renderOrderFacts(d);

  /* ---- 右栏：AI 决策建议 ---- */
  document.getElementById("dDecision").innerHTML = `
    <div class="decision-kv"><b>建议动作</b>${esc(ai.recommended_action)}</div>
    <div class="decision-kv"><b>是否转人工</b>${ai.handoff_required ? "是 — 必须创建人工审核工单" : "否 — AI 可直接回复"}</div>
    <div class="decision-kv"><b>最高风险</b>${esc(ai.risk_level)}（多规则命中取最高）</div>`;

  /* ---- 右栏：工单操作 ---- */
  document.getElementById("dTicketOps").innerHTML = renderTicketOps(d);
}

function buildTimeline(d) {
  const items = [];
  items.push({ time: "—", text: "会话进入系统，状态：待处理", cls: "done" });
  if (d.ticket) {
    (d.ticket.audit_log || []).forEach(a => {
      items.push({ time: a.time, text: `${a.actor === "supervisor" ? "主管" : "客服"}：${a.action} — ${a.detail}`, cls: "done" });
    });
  }
  items.push({ time: "当前", text: `会话状态：${d.conversation_status}`, cls: "active" });
  return items.map(i => `
    <div class="tl-item ${i.cls}">
      <span class="tl-dot"></span>
      <div class="tl-time">${esc(i.time)}</div>
      <div class="tl-text">${esc(i.text)}</div>
    </div>`).join("");
}

function renderOrderFacts(d) {
  // 隐私 / 他人订单：隐藏订单事实并显示安全提示（PRD FR-02）
  const isPrivacy = d.ai_decision.applied_rule_ids.includes("L-08");
  if (isPrivacy) {
    return `<div class="security-alert">🔒 隐私保护：该请求涉及他人订单，已隐藏全部订单与物流事实，拒绝在任何渠道披露。请引导账户持有人通过已验证渠道联系客服。</div>`;
  }
  const order = d.order || {};
  const logi = d.logistics || {};
  const hasOrder = Object.keys(order).length > 0;
  let html = "";
  if (!hasOrder) {
    html += `<div class="info-alert">⚠️ 订单号缺失 — 不展示、不猜测任何订单事实。请先请求客户补充完整订单号。</div>`;
  } else {
    html += `<div class="kv-list">
      <dt>订单号</dt><dd>${esc(order.order_id)}</dd>
      <dt>商品</dt><dd>${(order.items || []).map(i => esc(i.name) + " ×" + i.quantity).join("，")}</dd>
      <dt>金额</dt><dd>${esc(order.currency)} ${order.order_amount}</dd>
      <dt>下单日期</dt><dd>${esc(order.order_date)}</dd></div>`;
  }
  if (Object.keys(logi).length) {
    html += `<div class="divider"></div><div class="kv-list">
      <dt>物流商</dt><dd>${esc(logi.carrier || "—")}</dd>
      <dt>状态</dt><dd>${esc(logi.status)}</dd>
      <dt>最近节点</dt><dd>${esc(logi.last_event)}</dd>
      <dt>更新时间</dt><dd>${esc(logi.last_update)}</dd>
      <dt>未更新</dt><dd><b style="color:${logi.days_without_update >= 8 ? "var(--danger)" : "var(--warning)"}">${logi.days_without_update} 天</b></dd></div>`;
    if (logi.delivery_proof) {
      html += `<div class="divider"></div><div class="info-alert">📦 签收记录：${esc(logi.delivery_proof)} — 请客户核对配送地址与周边位置（门廊/邮箱/邻居），配送记录将进入人工审核。</div>`;
    }
  } else if (hasOrder) {
    html += `<div class="divider"></div><p class="kv-empty">物流信息待核实</p>`;
  }
  return html;
}

/* ---------------- 中栏：回复操作（按状态机分流） ---------------- */

function renderReplyActions(d) {
  const ai = d.ai_decision;
  const el = document.getElementById("dReplyActions");
  const status = d.conversation_status;
  const btn = (label, fn, cls = "btn-primary") =>
    `<button class="btn ${cls}" onclick="${fn}">${label}</button>`;

  if (status === "已解决" || status === "已关闭") {
    el.innerHTML = `<p class="block-hint">✅ 该会话已解决，无需进一步操作。</p>`;
    return;
  }
  if (status === "待客户补充") {
    el.innerHTML = `<p class="block-hint">⏳ 等待客户补充完整订单号。收到后会重新进行订单 / 物流 / 规则判断。</p>`;
    return;
  }
  if (ai.recommended_action === "request_order_information") {
    el.innerHTML = btn("发送补充订单号请求", "resolveCase('request_order_information')");
    return;
  }
  if (ai.handoff_required) {
    if (!d.ticket) {
      el.innerHTML = `<p class="block-hint">该案例为中/高风险，必须先创建人工审核工单并完成主管审批，才能发送最终回复。高风险会话不提供「直接关闭」。</p>`;
      return;
    }
    if (d.ticket.status === "待人工审核") {
      el.innerHTML = `<p class="block-hint">工单 ${esc(d.ticket.ticket_id)} 待主管审批。审批通过后此处可编辑并发送最终回复。</p>`;
      return;
    }
    if (d.ticket.status === "处理中") {
      if (!d.quality_passed) {
        el.innerHTML = `<p class="block-hint">主管已审批。发送前须先对当前回复运行 AI 质检并通过（quality_passed），否则后端将拒绝结案。</p>
          <button class="btn btn-primary" onclick="goQualityWithDraft()">前往 AI 质检 →</button>`;
        return;
      }
      el.innerHTML = btn("发送回复并解决", "resolveCase('send_reply')");
      return;
    }
    if (d.ticket.status === "已关闭") {
      el.innerHTML = `<p class="block-hint">工单已关闭。如客户再次追问，会话将重新进入待处理。</p>`;
      return;
    }
  }
  // 低风险：采用 / 编辑后发送
  el.innerHTML = btn("采用并发送（标记已解决）", "resolveCase('send_reply')");
}

/* ---------------- 右栏：工单操作 ---------------- */

function renderTicketOps(d) {
  const ai = d.ai_decision;
  if (!ai.handoff_required) {
    return `<p class="block-hint">该案例为低风险（无需人工审核），不创建工单。${ai.recommended_action === "request_order_information" ? "发送补充订单号请求后会话进入「待客户补充」。" : "采用或编辑回复后直接解决。"}</p>`;
  }
  if (!d.ticket) {
    return `
      <p class="block-hint" style="margin-bottom:12px">AI 判定必须人工审核。创建工单后由主管审批，客服不能自行批准高风险赔付方案。</p>
      <button class="btn btn-primary btn-block" onclick="openTicketModal()">创建人工审核工单</button>`;
  }
  const t = d.ticket;
  const audit = (t.audit_log || []).map(a =>
    `<div class="audit-entry">[${esc(a.time)}] ${esc(a.actor)} — ${esc(a.action)}：${esc(a.detail)}</div>`).join("");
  let ops = "";
  if (t.status === "待人工审核") {
    ops = `<button class="btn btn-primary btn-block" onclick="openApprove('${esc(t.ticket_id)}')">前往主管审批 →</button>`;
  } else if (t.status === "处理中") {
    ops = `<p class="block-hint" style="margin-top:10px">主管已审批（${esc(t.supervisor_decision)}）。请在中栏编辑并发送最终回复。</p>`;
  }
  return `
    <div class="ticket-head"><span class="ticket-id">${esc(t.ticket_id)}</span>${riskBadge(t.risk_level)}
      <span class="badge badge-status ${t.status === "处理中" ? "processing" : t.status === "已关闭" ? "resolved" : ""}">${esc(t.status)}</span></div>
    <div class="decision-kv"><b>原因</b>${esc(t.reason)}</div>
    <div class="decision-kv"><b>客服备注</b>${esc(t.agent_note || "无")}</div>
    <div class="decision-kv"><b>主管决策</b>${esc(t.supervisor_decision || "待审批")}</div>
    ${t.supervisor_note ? `<div class="decision-kv"><b>主管备注</b>${esc(t.supervisor_note)}</div>` : ""}
    <div class="audit-log">${audit}</div>${ops}`;
}

/* ---------------- P4 创建工单弹窗 ---------------- */

function openTicketModal() {
  if (!currentDetail) return;
  const d = currentDetail, ai = d.ai_decision;
  document.getElementById("mCaseId").textContent = d.case_id;
  document.getElementById("mCustomer").textContent = `${d.customer.alias} · ${d.customer.country}`;
  document.getElementById("mReason").textContent =
    ai.applied_rule_names.join("；") || ai.intent;
  document.getElementById("mRisk").innerHTML = riskBadge(ai.risk_level);
  document.getElementById("mAction").textContent = ai.recommended_action;
  document.getElementById("mPriority").textContent = ai.risk_level === "高" ? "高优先级" : "普通";
  document.getElementById("mAgentNote").value = "";
  document.getElementById("ticketModal").classList.remove("hidden");
}

function closeTicketModal() {
  document.getElementById("ticketModal").classList.add("hidden");
}

async function submitCreateTicket() {
  const note = document.getElementById("mAgentNote").value.trim();
  try {
    const t = await api("/api/tickets", {
      method: "POST",
      body: JSON.stringify({ case_id: currentCaseId, agent_note: note || null }),
    });
    closeTicketModal();
    toast(`工单 ${t.ticket_id} 已创建（待人工审核）`);
    await refreshDetail();
    showView("tickets"); // 对齐 Axure：关闭弹窗后跳转工单中心
  } catch (e) { toast("创建工单失败：" + e.message, true); }
}

async function refreshDetail() {
  if (!currentCaseId) return;
  try {
    currentDetail = await api("/api/cases/" + encodeURIComponent(currentCaseId));
    renderDetail(currentDetail);
  } catch (e) { toast(e.message, true); }
}

/* ---------------- resolve（低风险 / 审批后 / 补充信息） ---------------- */

async function resolveCase(action) {
  const body = { action };
  if (action === "send_reply") {
    const el = document.getElementById("replyDraft");
    if (!el || !el.value.trim()) { toast("请先填写回复内容", true); return; }
    body.reply = el.value.trim();
  } else {
    body.reply = "Could you please provide your full order number (starting with CF)?";
  }
  try {
    const d = await api(`/api/cases/${encodeURIComponent(currentCaseId)}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    toast(action === "send_reply" ? "回复已发送，会话已解决" : "已发送补充订单号请求");
    currentDetail = d;
    renderDetail(d);
  } catch (e) { toast("操作失败：" + e.message, true); }
}

/* ============================================================
   P5 工单中心 / P6 主管审批
   ============================================================ */

async function loadTickets() {
  const status = document.getElementById("filterTicketStatus").value;
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    const tickets = await api("/api/tickets" + params);
    const listEl = document.getElementById("ticketList");
    document.getElementById("ticketEmpty").classList.toggle("hidden", tickets.length > 0);
    listEl.innerHTML = tickets.map(t => `
      <div class="card">
        <div class="ticket-head">
          <span class="ticket-id">${esc(t.ticket_id)}</span>
          ${riskBadge(t.risk_level)}
          <span class="badge badge-status ${t.status === "处理中" ? "processing" : t.status === "已关闭" ? "resolved" : ""}">${esc(t.status)}</span>
          <span class="muted">来源会话：${esc(t.case_id)}</span>
        </div>
        <div class="decision-kv"><b>原因</b>${esc(t.reason)}</div>
        <div class="decision-kv"><b>客服备注</b>${esc(t.agent_note || "无")}</div>
        <div class="decision-kv"><b>主管决策</b>${esc(t.supervisor_decision || "待审核")}</div>
        ${t.status === "待人工审核"
          ? `<div class="row-gap"><button class="btn btn-primary" onclick="openApprove('${esc(t.ticket_id)}')">进入主管审批 →</button></div>`
          : `<div class="row-gap"><button class="btn btn-ghost" onclick="openDetail('${esc(t.case_id)}')">查看来源会话</button></div>`}
      </div>`).join("");
  } catch (e) { toast("工单加载失败：" + e.message, true); }
}

async function openApprove(ticketId) {
  currentTicketId = ticketId;
  showView("approve");
  document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
  document.getElementById("approveTicketLabel").textContent = "#" + ticketId;
  document.getElementById("approveContent").innerHTML = `<p class="muted">加载中…</p>`;
  try {
    const tickets = await api("/api/tickets");
    const ticket = tickets.find(t => t.ticket_id === ticketId);
    if (!ticket) throw new Error("工单不存在");
    const detail = await api("/api/cases/" + encodeURIComponent(ticket.case_id));
    renderApprove(ticket, detail);
  } catch (e) {
    document.getElementById("approveContent").innerHTML =
      `<div class="card"><p class="muted">${esc(e.message)}</p></div>`;
  }
}

function renderApprove(ticket, detail) {
  const ai = detail.ai_decision;
  const alreadyApproved = ticket.supervisor_decision !== null;

  const decisions = [
    ["要求补充材料", "向客服与客户要求补充必要材料后再审"],
    ["批准补发建议", "批准补发方案，交由客服执行后续沟通"],
    ["批准退款建议", "批准退款方案，交由客服执行后续沟通"],
    ["驳回并改写回复", "驳回当前建议，要求改写回复"],
  ];

  document.getElementById("approveContent").innerHTML = `
    <div class="approve-grid">
      <div>
        <div class="card">
          <h3 class="card-title">订单物流事实</h3>
          ${renderOrderFacts(detail)}
        </div>
      </div>
      <div>
        <div class="card ai-card">
          <h3 class="card-title">AI 建议 · 规则依据 · 客服备注</h3>
          <div class="decision-row">${riskBadge(ai.risk_level)}
            <span class="muted">意图：${esc(ai.intent)}</span>
            <span class="muted">情绪：${esc(ai.sentiment)}</span></div>
          <div class="decision-kv"><b>建议动作</b>${esc(ai.recommended_action)}</div>
          <div class="decision-kv"><b>命中规则</b></div>
          ${ai.applied_rule_names.map((n, i) => `<span class="rule-chip">${esc(ai.applied_rule_ids[i])} · ${esc(n)}</span>`).join("") || '<span class="muted">无</span>'}
          <div class="divider"></div>
          <div class="decision-kv"><b>客服备注</b>${esc(ticket.agent_note || "无")}</div>
          <div class="divider"></div>
          <div class="decision-kv"><b>回复草稿（参考）</b></div>
          <div class="reply-draft">${esc(ai.reply_draft)}</div>
        </div>
      </div>
      <div>
        <div class="card">
          <h3 class="card-title">主管操作</h3>
          ${alreadyApproved ? `
            <div class="success-alert">✅ 该工单已完成审批：<b>${esc(ticket.supervisor_decision)}</b><br>
            ${esc(ticket.supervisor_note || "")}<br>工单与会话状态均已更新为「处理中」。</div>
            <div class="row-gap">
              <button class="btn btn-primary" onclick="openDetail('${esc(ticket.case_id)}')">回到会话详情 →</button>
            </div>` : `
            <p class="muted" style="margin-bottom:12px">选择处理决策（审批后工单与会话进入「处理中」，并写入审计日志）：</p>
            <div class="decision-btn-list">
              ${decisions.map(([d, sub]) => `
                <button class="decision-btn" data-decision="${esc(d)}" onclick="selectDecision(this)">
                  ${esc(d)}<span class="btn-sub">${esc(sub)}</span>
                </button>`).join("")}
            </div>
            <label class="field-label">主管备注</label>
            <textarea id="approveNote" rows="3" placeholder="例如：物流停滞超过12天，批准补发建议。"></textarea>
            <div class="row-gap">
              <button class="btn btn-primary btn-block" id="approveSubmit" disabled onclick="submitApprove()">提交审批</button>
            </div>`}
        </div>
      </div>
    </div>`;
}

let selectedDecision = null;
function selectDecision(btn) {
  document.querySelectorAll(".decision-btn").forEach(b => b.classList.remove("selected"));
  btn.classList.add("selected");
  selectedDecision = btn.dataset.decision;
  document.getElementById("approveSubmit").disabled = false;
}

async function submitApprove() {
  if (!selectedDecision || !currentTicketId) return;
  const note = document.getElementById("approveNote").value.trim();
  try {
    const t = await api(`/api/tickets/${encodeURIComponent(currentTicketId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ decision: selectedDecision, supervisor_note: note || null }),
    });
    toast(`工单 ${t.ticket_id} 审批完成：${selectedDecision}`);
    // 对齐 Axure：审批后跳回会话详情的「审核完成」状态
    await openDetail(t.case_id);
  } catch (e) { toast("审批失败：" + e.message, true); }
}

/* ============================================================
   P7 AI 质检
   ============================================================ */

let qcResultCache = null;

async function loadQuality() {
  try {
    const cases = await api("/api/cases");
    const sel = document.getElementById("qcCaseSelect");
    const prev = sel.value || (currentCaseId || cases[0].case_id);
    sel.innerHTML = cases.map(c =>
      `<option value="${esc(c.case_id)}">${esc(c.case_id)} · ${esc(c.customer_alias)} · ${esc(c.issue_type)} · ${esc(c.risk_level)}风险</option>`).join("");
    if (cases.some(c => c.case_id === prev)) sel.value = prev;
    onQcCaseChange(false);
  } catch (e) { toast("质检页加载失败：" + e.message, true); }
}

function onQcCaseChange(clearReply = true) {
  const sel = document.getElementById("qcCaseSelect");
  const replyEl = document.getElementById("qcReply");
  if (clearReply) replyEl.value = "";
  if (qcDraftCarry) {           // 从会话详情带入的草稿
    if (sel.value === currentCaseId || !currentCaseId) {
      replyEl.value = qcDraftCarry;
      qcDraftCarry = "";
    }
  }
  document.getElementById("qcResult").innerHTML = `<p class="muted">尚未运行质检。</p>`;
  document.getElementById("qcSendAction").innerHTML = "";
  qcResultCache = null;
}

function fillQcDraft() {
  const sel = document.getElementById("qcCaseSelect");
  api("/api/cases/" + encodeURIComponent(sel.value)).then(d => {
    document.getElementById("qcReply").value = d.ai_decision.reply_draft;
  }).catch(e => toast(e.message, true));
}

function goQualityWithDraft() {
  qcDraftCarry = document.getElementById("replyDraft").value;
  showView("quality");
  // showView 已触发 loadQuality；草稿在 onQcCaseChange 中回填
  setTimeout(() => {
    const sel = document.getElementById("qcCaseSelect");
    if (sel && currentCaseId) sel.value = currentCaseId;
    if (qcDraftCarry) document.getElementById("qcReply").value = qcDraftCarry;
  }, 400);
}

async function runQualityCheck() {
  const caseId = document.getElementById("qcCaseSelect").value;
  const reply = document.getElementById("qcReply").value.trim();
  if (!reply) { toast("请输入要质检的回复内容", true); return; }
  try {
    const r = await api("/api/quality-check", {
      method: "POST",
      body: JSON.stringify({ case_id: caseId, reply }),
    });
    qcResultCache = { caseId, result: r, reply };
    const items = r.checks.map(c => `
      <div class="qc-item ${c.passed ? "pass" : "fail"}">
        <span>${c.passed ? "✔" : "✘"}</span><span>${esc(c.name)}</span>
      </div>`).join("");
    document.getElementById("qcResult").innerHTML = `
      ${items}
      <div class="qc-overall" style="color:${r.overall_passed ? "var(--success)" : "var(--danger)"}">
        ${r.overall_passed ? "✅ 质检通过" : "❌ 质检未通过 — 请修改回复后重试；质检不通过时不得关闭会话"}
      </div>`;
    renderQcSendAction(caseId, r);
  } catch (e) { toast("质检失败：" + e.message, true); }
}

async function renderQcSendAction(caseId, result) {
  const box = document.getElementById("qcSendAction");
  if (!result.overall_passed) { box.innerHTML = ""; return; }
  // 只有可解决的会话才显示「确认发送并关闭会话」
  try {
    const d = await api("/api/cases/" + encodeURIComponent(caseId));
    const ai = d.ai_decision;
    const resolvable =
      d.conversation_status !== "已解决" && d.conversation_status !== "已关闭" &&
      (ai.recommended_action === "request_order_information"
        ? "request_order_information"
        : (!ai.handoff_required || (d.ticket && d.ticket.supervisor_decision !== null))
          ? "send_reply" : null);
    if (!resolvable) {
      box.innerHTML = `<p class="block-hint">质检通过。但该会话当前不可关闭（中/高风险需创建工单并完成主管审批）。</p>`;
      return;
    }
    box.innerHTML = `
      <div class="divider"></div>
      <button class="btn btn-success btn-block" onclick="confirmSendAndClose('${esc(caseId)}','${esc(resolvable)}')">
        确认发送并关闭会话 →
      </button>
      <p class="note muted">发送仅更新模拟会话记录，不向真实客户发送消息；完成后跳转运营看板。</p>`;
  } catch (e) { box.innerHTML = ""; }
}

async function confirmSendAndClose(caseId, action) {
  const reply = document.getElementById("qcReply").value.trim();
  const body = action === "send_reply"
    ? { action: "send_reply", reply }
    : { action: "request_order_information", reply };
  try {
    await api(`/api/cases/${encodeURIComponent(caseId)}/resolve`, {
      method: "POST", body: JSON.stringify(body),
    });
    toast(action === "send_reply" ? "回复已发送，会话已解决" : "已发送补充订单号请求");
    showView("dashboard"); // 对齐 Axure：质检通过关单后跳转看板
  } catch (e) { toast("操作失败：" + e.message, true); }
}

/* ============================================================
   P8 运营看板
   ============================================================ */

async function loadDashboard() {
  try {
    const d = await api("/api/dashboard");

    // 成功提示条：主流程完成后展示数据同步提示
    document.getElementById("dashBanner").innerHTML = d.today_resolved > 0
      ? `<div class="success-alert dash-banner">✅ 已有 ${d.today_resolved} 个会话在本轮演示中解决 — 工单已关闭、看板指标已同步更新（模拟数据）。</div>`
      : `<div class="info-alert dash-banner">💡 完成高风险主流程（建单 → 审批 → 质检 → 关单）后，此处将显示数据同步提示。</div>`;

    document.getElementById("metricGrid").innerHTML = `
      <div class="metric-card"><div class="metric-label">今日已解决</div>
        <div class="metric-value">${d.today_resolved}</div></div>
      <div class="metric-card"><div class="metric-label">AI 建议采纳率</div>
        <div class="metric-value">${d.ai_adoption_rate}<span class="unit">%</span></div></div>
      <div class="metric-card"><div class="metric-label">人工介入率</div>
        <div class="metric-value">${d.human_intervention_rate}<span class="unit">%</span></div></div>
      <div class="metric-card"><div class="metric-label">回复合规率</div>
        <div class="metric-value">${d.reply_compliance_rate}<span class="unit">%</span></div></div>`;

    renderDonut("issueDonut", "issueLegend", d.issue_distribution);
    renderRiskBars(d.risk_distribution);
  } catch (e) { toast("看板加载失败：" + e.message, true); }
}

function renderDonut(svgId, legendId, items) {
  const svg = document.getElementById(svgId);
  const legend = document.getElementById(legendId);
  const total = items.reduce((a, b) => a + b.value, 0) || 1;
  const R = 62, C = 2 * Math.PI * R;
  let offset = 0;
  let circles = `<circle cx="80" cy="80" r="${R}" fill="none" stroke="#F1F5F9" stroke-width="20"/>`;
  items.forEach((item, i) => {
    const frac = item.value / total;
    const len = frac * C;
    circles += `
      <circle cx="80" cy="80" r="${R}" fill="none"
        stroke="${DONUT_COLORS[i % DONUT_COLORS.length]}"
        stroke-width="20" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}"
        stroke-dashoffset="${(-offset).toFixed(2)}"
        transform="rotate(-90 80 80)"/>`;
    offset += len;
  });
  circles += `
    <text x="80" y="76" text-anchor="middle" class="donut-center">${total}</text>
    <text x="80" y="94" text-anchor="middle" class="donut-center-sub">案例总数</text>`;
  svg.innerHTML = circles;
  legend.innerHTML = items.map((item, i) => `
    <div class="legend-item">
      <span class="legend-dot" style="background:${DONUT_COLORS[i % DONUT_COLORS.length]}"></span>
      <span>${esc(item.label)}</span>
      <span class="legend-val">${item.value}</span>
    </div>`).join("");
}

function renderRiskBars(items) {
  const max = Math.max(...items.map(i => i.value), 1);
  const colors = { "高风险": "red", "中风险": "orange", "低风险": "green" };
  document.getElementById("riskBars").innerHTML = items.map(item => `
    <div class="bar-row"><span>${esc(item.label)}</span>
      <div class="bar-track"><div class="bar-fill ${colors[item.label] || "green"}"
        style="width:${Math.round(item.value / max * 100)}%"></div></div>
      <span class="bar-value">${item.value}</span></div>`).join("");
}

/* ---------------- 初始化 ---------------- */

checkApi();
showView("home");
