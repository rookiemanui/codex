"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const cash = (cents) =>
  (Number(cents || 0) / 100).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const number = (milli) =>
  (Number(milli) / 1000).toLocaleString("zh-CN", { maximumFractionDigits: 3 });
const kindNames = {
  expense: "支出",
  income: "收入",
  transfer: "转账",
  refund: "退款",
  sale_refund: "销售退款",
};
const dateInput = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const uid = () =>
  window.crypto?.randomUUID
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
const state = {
  csrf: "",
  page: "overview",
  month: localStorage.getItem("ledger-month") || dateInput().slice(0, 7),
  options: { accounts: [], categories: [] },
  transactions: [],
  receipts: [],
  products: [],
  selectedReceipt: null,
  search: "",
  kindFilter: "all",
  poll: null,
  searchTimer: null,
  transactionRequest: 0,
  reviewEdits: new Map(),
};
let modalContext = null;
const iconPaths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  book: '<path d="M5 4h15v17H6a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2Z"/><path d="M3 17h17M8 8h7M8 12h5"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
  box: '<path d="m12 3 9 5v9l-9 5-9-5V8l9-5Z"/><path d="m3 8 9 5 9-5M12 13v9M7 5.8l9 5"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  rule: '<path d="M8 4h12M8 12h12M8 20h12M3 4h.1M3 12h.1M3 20h.1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  upload:
    '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  down: '<path d="M12 3v14m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  left: '<path d="m14 6-6 6 6 6"/>',
  right: '<path d="m10 6 6 6-6 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  wallet:
    '<path d="M3 6h16v14H3V6Zm0 0 13-3v3M16 11h5v5h-5a2.5 2.5 0 0 1 0-5Z"/>',
  income: '<path d="M12 3v13m-5-5 5 5 5-5M4 18v3h16v-3"/>',
  expense: '<path d="M12 17V4m-5 5 5-5 5 5M4 18v3h16v-3"/>',
  transfer: '<path d="M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4"/>',
  refund: '<path d="M4 10h10a6 6 0 0 1 0 12M4 10l5-5M4 10l5 5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  edit: '<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6Z"/>',
  leaf: '<path d="M20 3C8 3 3 6 3 13a7 7 0 0 0 14 0c0-3 0-6 3-10ZM3 21 15 9"/>',
};
function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${iconPaths[name] || iconPaths.book}</svg>`;
}
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `visible${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = ""), 5000);
}
async function api(path, body) {
  const options = { headers: {} };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-Local-CSRF": state.csrf,
    };
    options.body = JSON.stringify(body);
  }
  const result = await fetch(path, options);
  let value;
  try {
    value = await result.json();
  } catch {
    throw new Error("本地服务返回异常，请检查电脑上的启动窗口");
  }
  if (!result.ok) {
    if (result.status === 401) {
      if (state.authMode === "direct") boot();
      else loginScreen();
    }
    const error = new Error(value.error || "操作失败");
    error.status = result.status;
    throw error;
  }
  return value;
}
function accessibleForms(root = document) {
  root.querySelectorAll("label.form-field").forEach((label) => {
    const control = label.querySelector(
      "input:not([type=hidden]), select, textarea",
    );
    const title = label.querySelector(":scope > span");
    if (control && title && !control.hasAttribute("aria-label")) {
      control.setAttribute("aria-label", title.textContent.trim());
    }
  });
}
function backupNotice(result, normal = "已保存，并完成备份") {
  toast(
    result.backup_ok === false ? result.backup_warning : normal,
    result.backup_ok === false,
  );
}
async function boot() {
  try {
    const result = await api("/api/status");
    state.csrf = result.csrf;
    state.ledgerId = result.ledger_id;
    state.authMode = result.auth_mode;
    state.testMode = result.test_mode;
    if (result.authenticated) {
      await shell();
    } else loginScreen();
  } catch (error) {
    $("#app").innerHTML =
      `<div class="login-wrap"><div class="login-card"><h1>暂时无法连接账本</h1><p>${esc(error.message)}</p><button class="primary full" id="retry-start">重试</button></div></div>`;
    $("#retry-start").onclick = boot;
  }
}
function loginScreen() {
  if (state.authMode === "direct") {
    boot();
    return;
  }
  clearInterval(state.poll);
  closeModal();
  $("#app").innerHTML =
    `<div class="login-wrap"><form class="login-card" id="login-form"><div class="login-brand"><img src="/icon.svg" alt=""><strong>小锅账本</strong></div><div class="eyebrow">YOUR SHOP, YOUR DATA</div><h1>连接店铺账本</h1><p>输入你在电脑上设置的访问密码。</p><label class="form-field"><span>访问密码</span><input class="code-input" id="access-code" name="code" type="password" autocomplete="current-password" maxlength="256" aria-label="访问密码" required placeholder="······"></label><button class="primary full" type="submit">进入账本 ${icon("arrow")}</button><div class="form-error" id="login-error" role="alert"></div><small>${icon("shield")} 数据保存在电脑，无需云端账号</small></form></div>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", e.target);
    button.disabled = true;
    try {
      await api("/api/login", { code: $("#access-code").value });
      await boot();
    } catch (error) {
      $("#login-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  $("#access-code").focus();
}
async function shell() {
  state.options = await api("/api/options");
  $("#app").innerHTML =
    `<div class="shell"><aside class="sidebar"><div class="brand"><img src="/icon.svg" alt=""><div><strong>小锅账本</strong><small>LOCAL LEDGER</small></div></div><nav class="nav" aria-label="主导航">${[
      ["overview", "grid", "总览"],
      ["pos", "wallet", "餐饮收银"],
      ["orders", "book", "销售小票"],
      ["transactions", "book", "收支账目"],
      ["receipts", "image", "截图收纳"],
      ["inventory", "box", "库存管理"],
      ["rules", "rule", "分类规则"],
      ["settings", "shield", "备份与设置"],
    ]
      .map(
        ([page, i, label]) =>
          `<button data-page="${page}" class="${page === state.page ? "active" : ""}">${icon(i)}<span class="nav-label">${label}</span></button>`,
      )
      .join(
        "",
      )}</nav><div class="sidebar-foot"><span class="local-dot"></span><span style="font-size:12px">数据留在本机</span><p>一笔一备份，经营更安心。</p></div></aside><main class="main"><div class="mobile-brand"><img src="/icon.svg" alt="">小锅账本</div><div id="content"></div></main></div>`;
  $$("[data-page]").forEach(
    (el) => (el.onclick = () => navigate(el.dataset.page)),
  );
  await render();
}
async function navigate(page) {
  clearTimeout(state.searchTimer);
  state.page = page;
  state.selectedReceipt = null;
  state.kindFilter = "all";
  state.search = "";
  await render();
  window.scrollTo({ top: 0, behavior: "instant" });
}
function monthPicker() {
  return `<div class="month-picker"><button class="month-prev" aria-label="上个月">${icon("left")}</button><input type="month" value="${state.month}" aria-label="账目月份" id="month-select"><button class="month-next" aria-label="下个月">${icon("right")}</button></div>`;
}
function header(title, subtitle = "", buttons = "") {
  return `${state.testMode ? '<div class="notice" role="status"><strong>本地测试版 · 模拟账本</strong>　数据保存在独立测试目录；可以练习记账、库存和收银。销售确认仅用于测试，不会发起真实扣款。</div>' : ""}<header class="topbar"><div><div class="eyebrow">LOCAL LEDGER / 小店经营</div><h1>${title}</h1>${subtitle ? `<p class="subtitle">${subtitle}</p>` : ""}</div><div class="actions">${buttons}</div></header>`;
}
function bindHeader() {
  if ($("#month-select")) {
    $("#month-select").onchange = (e) => {
      if (e.target.value) {
        state.month = e.target.value;
        localStorage.setItem("ledger-month", state.month);
        render();
      }
    };
    $(".month-prev").onclick = () => changeMonth(-1);
    $(".month-next").onclick = () => changeMonth(1);
  }
  $$("[data-new-entry]").forEach((el) => (el.onclick = () => entryModal()));
  $$("[data-upload]").forEach(
    (el) =>
      (el.onclick = (e) => {
        e.stopPropagation();
        $("#image-input").click();
      }),
  );
}
function changeMonth(delta) {
  const [y, m] = state.month.split("-").map(Number);
  const date = new Date(y, m - 1 + delta, 1);
  state.month = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  localStorage.setItem("ledger-month", state.month);
  render();
}
async function render() {
  clearInterval(state.poll);
  $$("[data-page]").forEach((el) =>
    el.classList.toggle("active", el.dataset.page === state.page),
  );
  const el = $("#content");
  if (!el) return;
  el.innerHTML =
    '<div class="loading"><span class="spinner"></span>正在读取小锅账本…</div>';
  try {
    if (state.page === "overview") await overview();
    else if (state.page === "transactions") await transactionsPage();
    else if (state.page === "receipts") await receiptsPage();
    else if (state.page === "pos") await posPage();
    else if (state.page === "orders") await ordersPage();
    else if (state.page === "inventory") await inventoryPage();
    else if (state.page === "rules") await rulesPage();
    else await settingsPage();
    bindHeader();
    accessibleForms();
    if (["overview", "transactions", "inventory"].includes(state.page)) {
      state.poll = setInterval(() => {
        const editing =
          $(".modal") ||
          ["INPUT", "SELECT", "TEXTAREA"].includes(
            document.activeElement?.tagName,
          );
        if (!editing && document.visibilityState === "visible") render();
      }, 10000);
    }
  } catch (error) {
    el.innerHTML = `<div class="notice">${esc(error.message)}</div><button id="page-retry">重新读取</button>`;
    $("#page-retry").onclick = render;
  }
}
function dropzone() {
  return `<div class="dropzone" tabindex="0" role="button" aria-label="选择或拖入账单截图"><div class="drop-icon">${icon("upload")}</div><strong>把账单截图放进来</strong><p>一张图可含多笔账单，核对后再入账</p><button class="primary small" data-upload>${icon("plus")} 选择截图</button></div><div class="helper-strip"><span>${icon("image")} 原图保留</span><span>${icon("check")} 入账前核对</span><span>${icon("shield")} 自动备份</span></div>`;
}
function bindDrop() {
  const box = $(".dropzone");
  if (!box) return;
  box.onclick = () => $("#image-input").click();
  box.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      $("#image-input").click();
    }
  };
  box.ondragover = (e) => {
    e.preventDefault();
    box.classList.add("dragging");
  };
  box.ondragleave = () => box.classList.remove("dragging");
  box.ondrop = (e) => {
    e.preventDefault();
    box.classList.remove("dragging");
    uploadFiles([...e.dataTransfer.files]);
  };
}
function txTable(rows, compact = false) {
  return `<div class="table-wrap"><table class="transaction-table"><thead><tr><th>用途 / 商户</th><th${compact ? ' class="mobile-hide"' : ""}>类目</th><th class="mobile-hide">账户</th><th class="mobile-hide">时间</th><th class="amount">金额</th></tr></thead><tbody>${rows.length ? rows.map((row) => `<tr class="clickable" data-tx="${row.id}"${row.voided_at ? ' style="opacity:.45"' : ""}><td><div class="tx-main"><span class="category-icon ${row.kind}">${icon(row.kind)}</span><div><strong>${esc(row.purpose || row.merchant || kindNames[row.kind])}</strong><div class="secondary">${esc(row.merchant || row.occurred_at.slice(0, 16))}${row.voided_at ? " · 已作废" : ""}${row.receipt_id ? " · 附原图" : ""}</div></div></td><td${compact ? ' class="mobile-hide"' : ""}><span class="tag">${esc(row.category_name || kindNames[row.kind])}</span></td><td class="mobile-hide">${esc(row.account_name)}${row.to_account_name ? " → " + esc(row.to_account_name) : ""}</td><td class="mobile-hide">${esc(row.occurred_at.slice(5, 16))}</td><td class="amount ${row.kind}">${["expense", "sale_refund"].includes(row.kind) ? "−" : row.kind === "transfer" ? "" : "+"} ¥${cash(row.amount_cents)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty-row"><div class="empty-icon">${icon("book")}</div><strong>这里会留下店铺的每一笔账</strong><p>导入一张截图，或点击“记一笔”开始。</p></td></tr>`}</tbody></table></div>`;
}
function bindTx(rows) {
  $$("[data-tx]").forEach(
    (el) =>
      (el.onclick = () =>
        detailModal(rows.find((row) => row.id === el.dataset.tx))),
  );
}
function dailyTable(days) {
  return `<div class="table-wrap"><table class="transaction-table"><thead><tr><th>日期</th><th class="amount">收入</th><th class="amount">净支出</th><th class="amount">收支结余</th></tr></thead><tbody>${days.length ? days.map((day) => `<tr><td>${esc(day.date.slice(5))}</td><td class="amount income">¥${cash(day.income_cents)}</td><td class="amount">¥${cash(day.expense_cents)}</td><td class="amount">¥${cash(day.income_cents - day.expense_cents)}</td></tr>`).join("") : '<tr><td colspan="4" class="empty-row">记录收支后，这里会按天汇总。</td></tr>'}</tbody></table></div>`;
}
async function overview() {
  const [summary, rows] = await Promise.all([
    api(`/api/summary?month=${state.month}`),
    api(`/api/transactions?month=${state.month}&limit=6`),
  ]);
  state.transactions = rows;
  $("#content").innerHTML =
    header(
      "店铺总览",
      "每一笔收支，都有迹可循。",
      `${monthPicker()}<button id="daily-check">每日核对</button><button data-upload>${icon("upload")} 导入截图</button><button class="primary" data-new-entry>${icon("plus")} 记一笔</button>`,
    ) +
    `<div class="stats"><div class="stat featured"><div class="stat-head">本月净支出<span class="stat-icon">${icon("expense")}</span></div><div class="stat-value"><span class="currency">¥</span>${cash(summary.expense_cents)}</div><div class="stat-foot">已扣除退款 ¥${cash(summary.refund_cents)}</div></div><div class="stat"><div class="stat-head">本月收入<span class="stat-icon">${icon("income")}</span></div><div class="stat-value"><span class="currency">¥</span>${cash(summary.income_cents)}</div><div class="stat-foot">退款、账户转账不计入收入</div></div><div class="stat"><div class="stat-head">收支结余<span class="stat-icon">${icon("wallet")}</span></div><div class="stat-value"><span class="currency">¥</span>${cash(summary.net_cents)}</div><div class="stat-foot">收入减净支出，不等于经营利润</div></div><div class="stat"><div class="stat-head">待核对截图条目<span class="stat-icon">${icon("clock")}</span></div><div class="stat-value">${summary.pending_count}<span class="currency"> 笔</span></div><div class="stat-foot">${summary.low_stock_count} 项商品或原料需要补货</div></div></div><div class="grid-two"><section class="panel"><div class="panel-head"><h2>截图记账</h2><small>从一张凭证开始</small></div><div class="panel-body">${dropzone()}</div></section><section class="panel"><div class="panel-head"><h2>支出分类</h2><small>本月 · 扣除退款后</small></div><div class="panel-body">${
      summary.categories.length
        ? `<div class="bars">${summary.categories
            .slice(0, 6)
            .map(
              (row) =>
                `<div class="bar-row"><div class="bar-title"><span>${esc(row.name)}</span><b>¥${cash(row.amount_cents)}</b></div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, (row.amount_cents / Math.max(1, ...summary.categories.map((x) => x.amount_cents))) * 100))}%"></div></div></div>`,
            )
            .join("")}</div>`
        : `<div class="chart-empty"><div class="empty-icon">${icon("leaf")}</div>记下第一笔支出后，分类统计就会出现在这里。</div>`
    }</div></section></div><section class="panel"><div class="panel-head"><h2>最近账目 <small> / 本月 ${summary.transaction_count} 笔</small></h2><button class="quiet small" id="all-transactions">全部账目 ${icon("arrow")}</button></div>${txTable(rows.slice(0, 6), true)}</section><section class="panel" style="margin-top:22px"><div class="panel-head"><h2>每日收支</h2><button class="quiet small" id="all-days">本月日汇总 ${icon("arrow")}</button></div>${dailyTable(summary.days.slice(-7).reverse())}</section><p class="footer-note">${icon("shield")} 原始凭证与逐笔记录保存在本机 · 建议定期下载备份到另一块磁盘</p>`;
  bindDrop();
  $("#daily-check").onclick = () => dailyCheckModal();
  bindTx(rows);
  $("#all-transactions").onclick = () => navigate("transactions");
  $("#all-days").onclick = () =>
    modal("本月每日收支", dailyTable([...summary.days].reverse()), true);
}
async function transactionsPage() {
  const request = ++state.transactionRequest;
  const month = state.month,
    search = state.search;
  const rows = await api(
    `/api/transactions?month=${month}&search=${encodeURIComponent(search)}`,
  );
  if (
    state.page !== "transactions" ||
    month !== state.month ||
    search !== state.search ||
    request !== state.transactionRequest
  )
    return;
  state.transactions = rows;
  $("#content").innerHTML =
    header(
      "收支账目",
      "金额相同的不同交易会分别保留，作废记录也可以追溯。",
      `${monthPicker()}<a class="button-link" href="/api/export.csv"><button>${icon("down")} 导出 CSV</button></a><button class="primary" data-new-entry>${icon("plus")} 记一笔</button>`,
    ) +
    `<div class="filter-bar"><div class="search">${icon("search")}<input id="tx-search" placeholder="搜索用途、商户或交易单号" aria-label="搜索账目" value="${esc(state.search)}"></div><select id="tx-kind" aria-label="收支类型"><option value="all">全部收支</option>${Object.entries(
      kindNames,
    )
      .map(
        ([key, name]) =>
          `<option value="${key}"${state.kindFilter === key ? " selected" : ""}>${name}</option>`,
      )
      .join(
        "",
      )}</select><span class="secondary">共 ${rows.filter((x) => !x.voided_at).length} 笔有效记录</span></div><section class="panel" id="ledger-table">${txTable(rows.filter((x) => state.kindFilter === "all" || x.kind === state.kindFilter))}</section>`;
  bindTx(rows);
  $("#tx-kind").onchange = (e) => {
    state.kindFilter = e.target.value;
    $("#ledger-table").innerHTML = txTable(
      rows.filter(
        (x) => state.kindFilter === "all" || x.kind === state.kindFilter,
      ),
    );
    bindTx(rows);
  };
  $("#tx-search").oninput = (e) => {
    clearTimeout(state.searchTimer);
    state.search = e.target.value;
    state.searchTimer = setTimeout(async () => {
      if (state.page !== "transactions" || !e.target.isConnected || $(".modal"))
        return;
      const position = e.target.selectionStart;
      try {
        await transactionsPage();
        if (state.page !== "transactions" || $(".modal") || !$("#tx-search"))
          return;
        bindHeader();
        $("#tx-search").focus();
        $("#tx-search").setSelectionRange(position, position);
      } catch (error) {
        toast(error.message, true);
      }
    }, 350);
  };
}
function accountOptions(selected = "", blank = false) {
  return `${blank ? '<option value="">请选择付款账户</option>' : ""}${state.options.accounts.map((x) => `<option value="${x.id}"${x.id === selected ? " selected" : ""}>${esc(x.name)}</option>`).join("")}`;
}
function categoryOptions(kind, selected = "", blank = true) {
  return `${blank ? '<option value="">请选择类目</option>' : ""}${state.options.categories
    .filter((x) => x.kind === kind)
    .map(
      (x) =>
        `<option value="${x.id}"${x.id === selected ? " selected" : ""}>${esc(x.name)}</option>`,
    )
    .join("")}`;
}
function transactionFields(kind = "expense", value = {}) {
  return `<div class="form-grid"><label class="form-field"><span>金额（元）</span><input name="amount" inputmode="decimal" type="number" min="0.01" step="0.01" required placeholder="0.00" value="${esc(value.amount || "")}"></label><label class="form-field"><span>交易时间</span><input name="occurred_at" type="datetime-local" step="1" required value="${esc((value.occurred_at || "").replace(" ", "T"))}"></label><label class="form-field wide"><span>商品或用途</span><input name="purpose" placeholder="例如：鸡肉、小酥肉原料、采购饮料、顾客付款" value="${esc(value.purpose || "")}" maxlength="500"></label>${
    ["refund", "sale_refund"].includes(kind)
      ? `<label class="form-field wide"><span>关联原${kind === "sale_refund" ? "收入" : "支出"}</span><select name="refund_of" required><option value="">选择需要退款的原账目</option>${
          state.refundOptions
            ?.filter(
              (x) =>
                x.kind === (kind === "sale_refund" ? "income" : "expense") &&
                !x.voided_at &&
                x.amount_cents > x.refunded_cents,
            )
            .map(
              (x) =>
                `<option value="${x.id}"${value.refund_of === x.id ? " selected" : ""}>${esc(x.occurred_at.slice(0, 10))} · ${esc(x.purpose || x.merchant || "支出")} · 可退 ¥${cash(x.amount_cents - x.refunded_cents)}</option>`,
            )
            .join("") || ""
        }</select></label>`
      : kind === "transfer"
        ? ""
        : `<label class="form-field"><span>类目</span><select name="category_id" required>${categoryOptions(kind, value.category_id)}</select></label>`
  }<label class="form-field"><span>${kind === "transfer" ? "转出账户" : kind === "income" || kind === "refund" ? "收款账户" : "付款账户"}</span><select name="account_id" required>${accountOptions(value.account_id, true)}</select></label>${kind === "transfer" ? `<label class="form-field"><span>转入账户</span><select name="to_account_id" required>${accountOptions(value.to_account_id, true)}</select></label><div class="form-field wide"><small>自有账户之间转账、信用卡还款，不再计为消费。</small></div>` : ""}<label class="form-field"><span>商户 / 对方（选填）</span><input name="merchant" value="${esc(value.merchant || "")}" maxlength="500"></label><label class="form-field"><span>交易单号（选填）</span><input name="reference" value="${esc(value.reference || "")}" maxlength="200"></label><input type="hidden" name="platform" value="${esc(value.platform || "")}"><input type="hidden" name="time_precision" value="${esc(value.time_precision || ((value.occurred_at || "").length <= 16 ? "minute" : "second"))}"><input type="hidden" name="source_note" value="${esc(value.source_note || (value.notes ? "识别阶段提示（已由用户核对）：\n" + value.notes.join("\n") : ""))}">${["income", "expense"].includes(kind) ? `<label class="form-field wide check-line"><span><input type="checkbox" name="learn" value="yes"> 记住这次用途对应的分类</span><small>按具体用途学习，不把“淘宝”“抖音”等平台一律归为某类。</small></label>` : ""}</div>`;
}
function formData(form) {
  const value = Object.fromEntries(new FormData(form));
  if (value.learn === "yes") {
    value.learn_keyword = value.purpose;
  }
  delete value.learn;
  return value;
}
function modal(title, body, wide = false) {
  clearTimeout(state.searchTimer);
  if (modalContext)
    modalContext.edited ||= modalValues() !== modalContext.initial;
  else
    modalContext = {
      focus: document.activeElement,
      overflow: document.body.style.overflow,
      edited: false,
    };
  $("#modal-root").innerHTML =
    `<div class="modal-backdrop"><section class="modal${wide ? " wide-modal" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="modal-head"><h2>${esc(title)}</h2><button class="quiet icon-button" id="close-modal" aria-label="关闭">${icon("close")}</button></div><div class="modal-body">${body}</div></section></div>`;
  $("#app").inert = true;
  document.body.style.overflow = "hidden";
  const dialog = $(".modal");
  modalContext.initial = modalValues();
  // Some dialogs add recipe fields synchronously after modal() returns.
  queueMicrotask(() => {
    if (dialog === $(".modal") && modalContext)
      modalContext.initial = modalValues();
  });
  $("#close-modal").onclick = requestCloseModal;
  accessibleForms($("#modal-root"));
  $(".modal-backdrop").onclick = (e) => {
    if (e.target === e.currentTarget) requestCloseModal();
  };
  const first = $("input:not([type=hidden]), select, button", $(".modal-body"));
  if (first) first.focus();
}
function modalValues() {
  return JSON.stringify(
    $$("form input,form select,form textarea", $("#modal-root")).map((el) => [
      el.name,
      el.value,
      el.checked,
    ]),
  );
}
function requestCloseModal() {
  if (!modalContext) return;
  if (modalContext.pending)
    return toast("请先核实上次保存结果，避免重复记账", true);
  if ($(".modal button[type=submit]:disabled"))
    return toast("正在保存，请等待结果后再关闭");
  if (
    (modalContext.edited || modalValues() !== modalContext.initial) &&
    !confirm("尚有未保存的内容，确定放弃并关闭？")
  )
    return;
  closeModal();
}
function closeModal() {
  if (!modalContext) return;
  const previous = modalContext;
  modalContext = null;
  $("#modal-root").innerHTML = "";
  $("#app").inert = false;
  document.body.style.overflow = previous.overflow;
  if (previous.focus?.isConnected) previous.focus.focus();
}
function validateEntryForm(form) {
  const invalid = [...form.elements].find(
    (el) => el.willValidate && !el.checkValidity(),
  );
  if (!invalid) return true;
  const name = invalid.getAttribute("aria-label") || "必填内容";
  $(".form-error", form).textContent = invalid.validity.valueMissing
    ? `请填写或选择${name}`
    : name === "金额（元）"
      ? "金额须大于0，最多两位小数，例如20.50"
      : `请检查${name}：${invalid.validationMessage}`;
  invalid.focus();
  return false;
}
async function entryModal(kind = "expense", defaults = {}, draft = null) {
  if (["refund", "sale_refund"].includes(kind)) {
    const previous = $(".modal");
    const body = previous && $(".modal-body", previous);
    if (body) body.inert = true;
    try {
      state.refundOptions = await api("/api/transactions");
      if (previous && previous !== $(".modal")) return;
    } catch (error) {
      toast(error.message, true);
      return;
    } finally {
      if (body) body.inert = false;
    }
  }
  draft ||= { common: defaults, kinds: {} };
  const values = {
    occurred_at: dateInput(),
    ...draft.common,
    ...draft.kinds[kind],
  };
  const idem = uid();
  modal(
    "记一笔",
    `<div class="kind-tabs">${Object.entries(kindNames)
      .filter(([key]) => key !== "sale_refund" || kind === "sale_refund")
      .map(
        ([k, v]) =>
          `<button type="button" data-kind="${k}" class="${kind === k ? "active" : ""}">${v}</button>`,
      )
      .join(
        "",
      )}</div><p class="note">手录收入的退款，请从原收入详情办理；收银订单请到“销售小票”退单。</p><form id="entry-form" novalidate><div class="form-error" id="entry-error" role="alert"></div>${transactionFields(kind, values)}<div class="form-footer"><button type="button" class="cancel-modal">取消</button><button class="primary" type="submit">${icon("check")} 保存并备份</button></div></form>`,
  );
  const form = $("#entry-form");
  let pending = null;
  const controls = [...$$("input,select,button", form), ...$$("[data-kind]")];
  if (values.learn === "yes" && $("[name=learn]", form))
    $("[name=learn]", form).checked = true;
  $$("input,select", form).forEach((el) =>
    el.setAttribute("aria-describedby", "entry-error"),
  );
  $("[name=amount]", form).focus();
  $$("[data-kind]").forEach(
    (el) =>
      (el.onclick = () => {
        if (el.dataset.kind === kind || $("button[type=submit]", form).disabled)
          return;
        const value = Object.fromEntries(new FormData(form));
        draft.kinds[kind] = value;
        for (const key of [
          "amount",
          "occurred_at",
          "purpose",
          "account_id",
          "merchant",
          "reference",
          "platform",
          "time_precision",
          "source_note",
        ])
          draft.common[key] = value[key];
        // Type-specific fields must not overwrite the latest shared fields.
        for (const key of Object.keys(draft.common))
          delete draft.kinds[kind][key];
        entryModal(el.dataset.kind, {}, draft);
      }),
  );
  $(".cancel-modal").onclick = requestCloseModal;
  $("#entry-form").onsubmit = async (e) => {
    e.preventDefault();
    if (!pending && !validateEntryForm(e.target)) return;
    const button = $("button[type=submit]", e.target);
    pending ||= { ...formData(e.target), kind, idempotency_key: idem };
    controls.forEach((el) => {
      el.disabled = true;
    });
    button.disabled = true;
    try {
      const result = await api("/api/transactions", pending);
      closeModal();
      backupNotice(result);
      await render();
      $("[data-new-entry]")?.focus();
    } catch (error) {
      if (!form.isConnected) return;
      if (error.status === 400) {
        pending = null;
        modalContext.pending = false;
        controls.forEach((el) => {
          el.disabled = false;
        });
      } else {
        modalContext.pending = true;
        button.textContent = "核实上次保存并备份";
      }
      $(".form-error", e.target).textContent = pending
        ? "保存结果尚未确认，请点“核实上次保存并备份”，请勿刷新、关页或重新开单。" +
          error.message
        : error.message;
    } finally {
      button.disabled = false;
    }
  };
}
async function detailModal(row) {
  modal(
    "账目详情",
    `<div class="detail-amount">${["expense", "sale_refund"].includes(row.kind) ? "−" : row.kind === "transfer" ? "" : "+"} ¥${cash(row.amount_cents)}</div><dl class="detail-list"><dt>交易类型</dt><dd>${kindNames[row.kind]}${row.voided_at ? " · 已作废" : ""}</dd><dt>用途</dt><dd>${esc(row.purpose || "未填写")}</dd><dt>交易时间</dt><dd>${esc(row.time_precision === "minute" ? row.occurred_at.slice(0, 16) : row.time_precision === "date" ? row.occurred_at.slice(0, 10) : row.occurred_at)}</dd><dt>时间精度</dt><dd>${{ minute: "记录精确到分钟", second: "记录精确到秒", date: "仅提供日期", unknown: "来源未标注精度" }[row.time_precision] || "来源未标注精度"}</dd><dt>账户</dt><dd>${esc(row.account_name)}${row.to_account_name ? " → " + esc(row.to_account_name) : ""}</dd><dt>商户</dt><dd>${esc(row.merchant || "未填写")}</dd><dt>交易单号</dt><dd>${esc(row.reference || "未提供")}</dd><dt>已退金额</dt><dd>¥${cash(row.refunded_cents)}</dd><dt>原始凭证</dt><dd>${row.receipt_id ? "已保留原图" : "本笔未附原图"}</dd></dl>${row.source_note ? `<div class="detail-section"><h3>来源说明</h3><p class="secondary" style="white-space:pre-wrap">${esc(row.source_note)}</p></div>` : ""}${row.receipt_id ? `<div class="detail-section detail-receipt"><h3>原始截图</h3><img src="/api/receipts/${row.receipt_id}/file" alt="本笔账目的原始凭证"></div>` : ""}${!row.voided_at && ["expense", "income"].includes(row.kind) ? `<div class="detail-section"><h3>修正用途或分类</h3><form id="reclassify-form"><div class="form-grid"><label class="form-field wide"><span>用途</span><input name="purpose" value="${esc(row.purpose)}"></label><label class="form-field wide"><span>类目</span><select name="category_id" required>${categoryOptions(row.kind, row.category_id)}</select></label><label class="check-line form-field wide"><span><input type="checkbox" name="learn" value="yes"> 记住这个用途关键词</span></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary small" type="submit">保存修改与备份</button></div></form></div>` : ""}${!row.voided_at ? `<div class="detail-section"><div class="form-footer">${["expense", "income"].includes(row.kind) && row.amount_cents > row.refunded_cents ? `<button id="refund-entry">登记退款</button>` : ""}<button class="danger" id="void-entry">作废此笔</button></div><p class="note">作废不会删除原始记录与备份。已关联退款或库存时，需要先核对关联记录。</p></div>` : ""}`,
  );
  if ($("#reclassify-form"))
    $("#reclassify-form").onsubmit = async (e) => {
      e.preventDefault();
      const button = $("button", e.target);
      button.disabled = true;
      try {
        const result = await api(
          `/api/transactions/${row.id}/category`,
          formData(e.target),
        );
        closeModal();
        backupNotice(result);
        await render();
      } catch (error) {
        $(".form-error", e.target).textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  if ($("#refund-entry"))
    $("#refund-entry").onclick = () =>
      entryModal(row.kind === "income" ? "sale_refund" : "refund", {
        refund_of: row.id,
        account_id: row.account_id,
        purpose: "退款：" + (row.purpose || row.merchant),
      });
  if ($("#void-entry"))
    $("#void-entry").onclick = async () => {
      if (!confirm("确认作废这笔账目？原始记录和备份会保留。")) return;
      try {
        const result = await api(`/api/transactions/${row.id}/void`, {});
        closeModal();
        backupNotice(result, "已作废，历史记录与备份已保留");
        await render();
      } catch (error) {
        toast(error.message, true);
      }
    };
}
async function uploadFiles(files) {
  if (!files.length) return;
  let last = null;
  for (const [i, file] of files.entries()) {
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      toast(`不支持 ${file.name}，请选择PNG、JPG或WebP`, true);
      continue;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast(`${file.name} 超过20MB`, true);
      continue;
    }
    toast(`正在保存原图 ${i + 1}/${files.length}…`);
    try {
      const content = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("无法读取图片"));
        reader.readAsDataURL(file);
      });
      const result = await api("/api/receipts", {
        filename: file.name,
        content,
      });
      last = result.id;
      backupNotice(
        result,
        result.duplicate_file
          ? "这张原图已经存在，已打开原有条目"
          : "原图已保存和备份，正在识别",
      );
    } catch (error) {
      toast(error.message, true);
    }
  }
  $("#image-input").value = "";
  if (last) {
    state.page = "receipts";
    state.selectedReceipt = last;
    await render();
  }
}
$("#image-input").onchange = (e) => uploadFiles([...e.target.files]);
async function receiptsPage() {
  if (state.selectedReceipt) {
    await receiptReview(state.selectedReceipt);
    return;
  }
  state.receipts = await api("/api/receipts");
  $("#content").innerHTML =
    header(
      "截图收纳",
      "先保存原始凭证，再核对每笔交易。",
      `<button class="primary" data-upload>${icon("upload")} 导入截图</button>`,
    ) +
    `<section class="panel"><div class="panel-body" style="padding-top:22px">${dropzone()}</div></section><div class="receipts-grid">${state.receipts.map((row) => `<article class="receipt-tile" data-receipt="${row.id}" tabindex="0"><div class="receipt-thumb"><img src="/api/receipts/${row.id}/file" alt="${esc(row.filename)}" loading="lazy"></div><div class="receipt-caption"><strong>${esc(row.filename)}</strong><p><span>${esc(row.created_at.slice(0, 10))}</span><span class="tag ${row.pending ? "warn" : row.status === "queued" ? "warn" : "good"}">${row.status === "queued" ? "识别中" : row.pending ? row.pending + "笔待核对" : row.status === "error" ? "需手工核对" : "已核对"}</span></p></div></article>`).join("")}</div>`;
  bindDrop();
  $$("[data-receipt]").forEach((el) => {
    el.onclick = () => {
      state.selectedReceipt = el.dataset.receipt;
      render();
    };
    el.onkeydown = (e) => {
      if (e.key === "Enter") el.click();
    };
  });
  if (state.receipts.some((x) => x.status === "queued"))
    state.poll = setInterval(async () => {
      const next = await api("/api/receipts");
      if (next.some((x, i) => x.status !== state.receipts[i]?.status)) {
        render();
      }
    }, 2500);
}
async function receiptReview(key) {
  const row = await api(`/api/receipts/${key}`);
  state.refundOptions = await api("/api/transactions");
  for (const draft of row.drafts) {
    const cacheKey = `${state.ledgerId}:${key}:${draft.id}`;
    if (draft.status === "posted") state.reviewEdits.delete(cacheKey);
    else {
      const saved = state.reviewEdits.get(cacheKey);
      if (saved) draft.data = { ...draft.data, ...saved.values };
    }
  }
  $("#content").innerHTML =
    header(
      "核对截图账单",
      row.filename,
      `<button id="back-receipts">${icon("left")} 返回截图</button>`,
    ) +
    `<div class="split-view"><div class="receipt-preview"><p>原始凭证 · 核对金额、时间和用途</p><img src="/api/receipts/${key}/file" alt="待核对的原始账单截图"></div><div class="review-stack">${row.status === "queued" ? `<div class="review-card loading"><span class="spinner"></span>本地识别中，图片已经保存和备份。<p class="note">首次加载组件可能需要一些时间。</p></div>` : ""}${row.error ? `<div class="notice">${esc(row.error)}</div>` : ""}${row.drafts
      .map(
        (draft, index) =>
          `<section class="review-card"><div class="review-head"><h3>第 ${index + 1} 笔账单</h3><span class="tag ${draft.status === "posted" ? "good" : "warn"}">${draft.status === "posted" ? "已入账" : "待核对"}</span></div>${
            draft.status === "posted"
              ? `<p class="secondary">本笔已经入账并备份，不会因为重新上传相同原图再次新增。</p>`
              : `${draft.data.notes?.length ? `<div class="review-notes">${draft.data.notes.map((note) => `<p>${esc(note)}</p>`).join("")}</div>` : ""}<form data-draft="${draft.id}" data-receipt="${key}" novalidate><label class="form-field" style="margin-bottom:16px"><span>收支方向</span><select name="kind" class="draft-kind">${Object.entries(
                  kindNames,
                )
                  .filter(([k]) => k !== "sale_refund")
                  .map(
                    ([k, n]) =>
                      `<option value="${k}"${k === (draft.data.kind || "expense") ? " selected" : ""}>${n}</option>`,
                  )
                  .join(
                    "",
                  )}</select></label><div class="draft-fields">${transactionFields(draft.data.kind || "expense", draft.data)}</div><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">${icon("check")} 确认本笔并入账</button></div></form>`
          }</section>`,
      )
      .join(
        "",
      )}<p class="note">系统不会仅凭相同金额合并交易。请逐笔核对；同一张截图可以保留多笔交易。销售退款请从销售小票选择原单办理退单。</p></div></div>`;
  $("#back-receipts").onclick = () => {
    state.selectedReceipt = null;
    render();
  };
  $$("[data-draft]").forEach((form) => {
    const id = form.dataset.draft;
    const cached = state.reviewEdits.get(`${state.ledgerId}:${key}:${id}`);
    form.reviewKinds = cached?.kinds || {};
    form.dataset.kind = $("[name=kind]", form).value;
    if (cached?.values.learn === "yes" && $("[name=learn]", form))
      $("[name=learn]", form).checked = true;
    const idem = uid();
    form.onsubmit = async (e) => {
      e.preventDefault();
      if (!validateEntryForm(form)) return;
      cacheReviewForm(form);
      const button = $("button[type=submit]", form);
      button.disabled = true;
      try {
        const result = await api("/api/transactions", {
          ...formData(form),
          receipt_id: key,
          draft_id: id,
          idempotency_key: idem,
        });
        backupNotice(result, "本笔已入账，原图和逐笔记录已备份");
        state.reviewEdits.delete(`${state.ledgerId}:${key}:${id}`);
        await render();
      } catch (error) {
        $(".form-error", form).textContent = error.message;
        button.disabled = false;
      }
    };
  });
  if (row.status === "queued")
    state.poll = setInterval(async () => {
      try {
        const next = await api(`/api/receipts/${key}`);
        if (next.status !== "queued") await render();
      } catch (error) {
        toast(error.message, true);
        clearInterval(state.poll);
      }
    }, 2200);
}
function cacheReviewForm(form) {
  state.reviewEdits.set(
    `${state.ledgerId}:${form.dataset.receipt}:${form.dataset.draft}`,
    {
      values: Object.fromEntries(new FormData(form)),
      kinds: form.reviewKinds || {},
    },
  );
}
async function inventoryPage() {
  const [products, movements] = await Promise.all([
    api("/api/products"),
    api("/api/movements"),
  ]);
  state.products = products;
  const low = products.filter((p) => p.low);
  $("#content").innerHTML =
    header(
      "库存管理",
      "商品与原料都能按自己的单位管理，数量支持三位小数。",
      `<a class="button-link" href="/api/inventory.csv"><button type="button">${icon("down")} 导出盘点表</button></a><button class="primary" id="new-product">${icon("plus")} 新建商品 / 原料</button>`,
    ) +
    `${
      low.length
        ? `<div class="notice">${icon("box")} ${low.length} 项库存达到补货线：${low
            .map((x) => esc(x.name))
            .slice(0, 5)
            .join("、")}${low.length > 5 ? "…" : ""}</div>`
        : ""
    }<section class="panel"><div class="panel-head"><h2>当前库存</h2><small>${products.length} 个商品 / 原料</small></div><div class="table-wrap"><table><thead><tr><th>商品 / 原料</th><th>现有库存</th><th class="mobile-hide">补货线</th><th>操作</th></tr></thead><tbody>${products.length ? products.map((p) => `<tr><td><strong>${esc(p.name)}</strong><div class="secondary">${esc(p.sku)}</div></td><td class="${p.low ? "stock-low" : ""}">${esc(p.stock)} ${esc(p.unit)}${p.low ? ' <span class="tag warn">待补货</span>' : ""}</td><td class="mobile-hide">${esc(p.minimum)} ${esc(p.unit)}</td><td><div class="row-actions"><button class="small" data-stock="${p.id}" data-kind="in">入库</button><button class="small" data-stock="${p.id}" data-kind="out">出库</button><button class="small quiet" data-stock="${p.id}" data-kind="adjust">盘点</button></div></td></tr>`).join("") : '<tr><td colspan="4" class="empty-row"><div class="empty-icon">' + icon("box") + "</div><strong>先添加一个需要管理的商品或原料</strong><p>例如鸡肉、小酥肉、食用油、打包盒，再登记入库或盘点数量。</p></td></tr>"}</tbody></table></div></section><section class="panel" style="margin-top:22px"><div class="panel-head"><h2>最近库存流水</h2><small>每次变动独立留档</small></div><div class="table-wrap"><table><thead><tr><th>商品 / 原料</th><th>动作</th><th>数量变动</th><th class="mobile-hide">说明 / 时间</th></tr></thead><tbody>${
      movements.length
        ? movements
            .slice(0, 30)
            .map(
              (m) =>
                `<tr><td>${esc(m.name)}</td><td><span class="tag">${esc({ in: "入库", out: "出库", adjust: "盘点", loss: "损耗", customer_return: "顾客退货", supplier_return: "退供应商", reversal: "撤销" }[m.kind])}</span></td><td>${m.delta_milli > 0 ? "+" : ""}${number(m.delta_milli)} ${esc(m.unit)}</td><td class="mobile-hide">${esc(m.note || "—")}<div class="secondary">${esc(m.occurred_at.slice(0, 16))}${m.transaction_id ? " · 已关联收支" : ""}</div></td></tr>`,
            )
            .join("")
        : '<tr><td colspan="4" class="empty-row">还没有库存变动记录。</td></tr>'
    }</tbody></table></div></section>`;
  $("#new-product").onclick = productModal;
  try {
    if (readPendingInventory()) {
      const notice = document.createElement("div");
      notice.className = "notice";
      notice.innerHTML = '上次库存操作结果待核实。 <button id="pending-inventory">核实上次操作</button>';
      $("#content").prepend(notice);
      $("#pending-inventory").onclick = retryPendingInventory;
    }
  } catch { toast("待核实记录无法读取，请保留浏览器数据。", true); }
  const rows = $$("section.panel:last-child tbody tr", $("#content"));
  movements.slice(0, 30).forEach((movement, index) => {
    const cell = rows[index]?.cells[1];
    if (!cell) return;
    if (movement.reversed_by || movement.reversal_of) {
      cell.append(document.createTextNode(movement.reversed_by ? " · 已撤销" : " · 撤销记录"));
    } else if (!String(movement.idempotency_key || "").startsWith("sale:") && ["in", "out", "loss", "adjust", "customer_return", "supplier_return"].includes(movement.kind)) {
      const button = document.createElement("button");
      button.className = "small quiet";
      button.textContent = "撤销录错";
      button.onclick = () => reverseMovementModal(movement);
      cell.append(button);
    }
  });
  $$("[data-stock]").forEach(
    (el) =>
      (el.onclick = () =>
        movementModal(
          products.find((p) => p.id === el.dataset.stock),
          el.dataset.kind,
        )),
  );
}
function productModal() {
  modal(
    "新建商品 / 原料",
    `<form id="product-form"><div class="form-grid"><label class="form-field wide"><span>名称</span><input name="name" required maxlength="100" placeholder="例如：鸡肉、小酥肉原料"></label><label class="form-field"><span>单位</span><input name="unit" value="件" required maxlength="20" placeholder="件、瓶、袋、千克"></label><label class="form-field"><span>补货线</span><input name="minimum" type="number" min="0" step="0.001" value="0" required></label><label class="form-field wide"><span>商品编码（选填）</span><input name="sku" placeholder="可填写条码或内部编码，留空自动生成" maxlength="100"></label></div><p class="note">新商品从零库存开始。已有库存请通过“盘点”登记，以便保留记录。</p><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">创建并备份</button></div></form>`,
  );
  bindSimpleForm("#product-form", "/api/products");
}
function pendingInventoryKey() {
  return "ledger-pending-inventory:" + state.ledgerId;
}
function readPendingInventory() {
  const raw = localStorage.getItem(pendingInventoryKey());
  return raw ? JSON.parse(raw) : null;
}
async function retryPendingInventory() {
  let pending;
  try {
    pending = readPendingInventory();
    if (!pending) return;
  } catch {
    toast("待核实记录无法读取，请保留浏览器数据并联系维护人员。", true);
    return;
  }
  modal("核实上次库存操作", '<p>上次操作结果尚未确认。将使用原来的内容核实，避免重复记录。</p><div class="form-error" role="alert"></div><button class="primary" id="retry-inventory">核实原请求</button>');
  $("#retry-inventory").onclick = async (event) => {
    event.target.disabled = true;
    try {
      const result = await api(pending.path, pending.value);
      localStorage.removeItem(pendingInventoryKey());
      closeModal();
      backupNotice(result);
      await render();
    } catch (error) {
      $(".form-error", $("#modal-root")).textContent = error.message;
      if (error.status === 400) {
        localStorage.removeItem(pendingInventoryKey());
        $(".form-error", $("#modal-root")).textContent += "。服务已拒绝此请求，请关闭后重新填写。";
      }
    } finally {
      event.target.disabled = false;
    }
  };
}
function movementModal(product, kind) {
  try {
    if (readPendingInventory()) return retryPendingInventory();
  } catch {
    return toast("无法读取待核实库存记录，请检查浏览器存储。", true);
  }
  const title = {in: "入库", out: "出库", adjust: "盘点"}[kind];
  const idem = uid();
  modal(
    title + " · " + product.name,
    '<form id="movement-form"><div class="form-grid">' +
    '<label class="form-field"><span>' + (kind === "adjust" ? "盘点后的实际数量" : "本次数量") + '（' + esc(product.unit) + '）</span><input name="quantity" type="number" min="' + (kind === "adjust" ? "0" : "0.001") + '" step="0.001" required placeholder="0"' + (kind === "adjust" ? ' value="' + esc(product.stock) + '"' : '') + '></label>' +
    '<label class="form-field"><span>发生时间</span><input name="occurred_at" type="datetime-local" step="1" value="' + dateInput() + '" required></label>' +
    '<label class="form-field wide"><span>说明</span><input name="note" placeholder="例如：鸡肉采购、日常领用、食材损耗"></label>' +
    (kind !== "adjust" ? '<label class="form-field wide"><span>库存动作</span><select name="kind">' + (kind === "in" ? [["in", "采购 / 收货入库"], ["customer_return", "顾客退货入库"]] : [["out", "销售 / 领用出库"], ["loss", "损耗出库"], ["supplier_return", "退供应商"]]).map(([key, label]) => '<option value="' + key + '">' + label + '</option>').join("") + '</select></label>' : '<p class="note wide">当前库存 ' + esc(product.stock) + ' ' + esc(product.unit) + '。若另一人已修改库存，本次盘点会被拒绝，请刷新后重新核对。</p>') +
    '</div>' +
    (kind !== "adjust" ? '<div class="detail-section" id="finance-section"><label class="check-line"><input type="checkbox" id="link-finance"> <span id="finance-label"></span></label><div id="linked-finance" hidden><div class="form-grid"><label class="form-field"><span>总金额（元）</span><input name="linked_amount" type="number" min="0.01" step="0.01" inputmode="decimal"></label><label class="form-field"><span>账户</span><select name="linked_account">' + accountOptions("cash") + '</select></label><label class="form-field wide"><span>收支类目</span><select name="linked_category"></select></label></div><p class="note">仅在尚未记过这笔收支时勾选。库存和收支一起保存。</p></div></div><p class="note" id="movement-tip"></p>' : '') +
    '<div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">确认' + title + '并备份</button></div></form>'
  );
  const form = $("#movement-form");
  let pending = null;
  const syncFinance = () => {
    const action = form.elements.kind?.value || kind;
    const supported = ["in", "out"].includes(action);
    $("#finance-section").hidden = !supported;
    $("#link-finance").checked = false;
    $("#linked-finance").hidden = true;
    form.elements.linked_amount.required = false;
    $("#finance-label").textContent = "同时新增一笔" + (action === "in" ? "采购支出" : "销售收入");
    form.elements.linked_category.innerHTML = categoryOptions(action === "in" ? "expense" : "income", action === "in" ? "ingredients" : "sales", false);
    $("#movement-tip").textContent = {loss: "损耗只减少库存，不自动产生收支。", customer_return: "此处只登记退回的库存。款项请从原收入登记退款。", supplier_return: "此处只登记退回的库存。收到退款后，请从原采购支出登记退款。", out: "厨房领用只登记出库；只有确实收到且尚未记账的销售款才勾选收入。"}[action] || "";
  };
  if (kind !== "adjust") {
    syncFinance();
    form.elements.kind.onchange = syncFinance;
    $("#link-finance").onchange = (e) => {
      $("#linked-finance").hidden = !e.target.checked;
      form.elements.linked_amount.required = e.target.checked;
    };
  }
  form.onsubmit = async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", form);
    button.disabled = true;
    try {
      if (!pending) {
        const fields = Object.fromEntries(new FormData(form));
        const action = fields.kind || kind;
        const value = {product_id: product.id, kind: action, quantity: fields.quantity, occurred_at: fields.occurred_at, note: fields.note, idempotency_key: idem};
        if (action === "adjust") value.expected_stock_version = product.stock_version;
        if ($("#link-finance")?.checked && ["in", "out"].includes(action)) {
          value.transaction = {kind: action === "in" ? "expense" : "income", amount: fields.linked_amount, account_id: fields.linked_account, category_id: fields.linked_category, occurred_at: fields.occurred_at, purpose: title + "：" + product.name, idempotency_key: idem + "-entry"};
        }
        const request = {path: "/api/movements", value};
        localStorage.setItem(pendingInventoryKey(), JSON.stringify(request));
        pending = request;
      }
      $$("input,select", form).forEach((el) => el.disabled = true);
      const result = await api(pending.path, pending.value);
      localStorage.removeItem(pendingInventoryKey());
      closeModal();
      backupNotice(result);
      await render();
    } catch (error) {
      $(".form-error", form).textContent = error.message;
      if (error.status === 400) {
        localStorage.removeItem(pendingInventoryKey());
        pending = null;
        $$("input,select", form).forEach((el) => el.disabled = false);
      } else if (pending) {
        $(".form-error", form).textContent += "。结果待核实，请重试原请求；关闭或刷新后也可在库存页继续核实。";
        button.textContent = "核实原请求";
      }
    } finally {
      button.disabled = false;
    }
  };
}
function reverseMovementModal(movement) {
  try {
    if (readPendingInventory()) return retryPendingInventory();
  } catch {
    return toast("无法读取待核实库存记录，请检查浏览器存储。", true);
  }
  const idem = uid();
  let pending = null;
  modal("撤销录错的库存记录", '<form id="reverse-movement-form"><p>将反向记录「' + esc(movement.name) + '」的这次库存变动' + (movement.transaction_id ? '，并作废关联收支' : '') + '。原记录仍会保留。已使用的库存可能无法撤销；实际退货请使用退货功能。</p><label class="form-field"><span>撤销原因</span><input name="reason" required maxlength="500" placeholder="例如：采购数量录错"></label><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">确认撤销并备份</button></div></form>');
  const form = $("#reverse-movement-form");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = $("button", form);
    button.disabled = true;
    try {
      if (!pending) {
        const request = {path: "/api/movements/" + movement.id + "/reverse", value: {reason: form.elements.reason.value, idempotency_key: idem}};
        localStorage.setItem(pendingInventoryKey(), JSON.stringify(request));
        pending = request;
      }
      form.elements.reason.disabled = true;
      const result = await api(pending.path, pending.value);
      localStorage.removeItem(pendingInventoryKey());
      closeModal();
      backupNotice(result);
      await render();
    } catch (error) {
      $(".form-error", form).textContent = error.message;
      if (error.status === 400) {
        localStorage.removeItem(pendingInventoryKey());
        pending = null;
        form.elements.reason.disabled = false;
      } else if (pending) {
        $(".form-error", form).textContent += "。结果待核实，请重试原请求。";
        button.textContent = "核实原请求";
      }
    } finally { button.disabled = false; }
  };
}

function bindSimpleForm(selector, path) {
  $(selector).onsubmit = async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", e.target);
    button.disabled = true;
    try {
      const result = await api(
        path,
        Object.fromEntries(new FormData(e.target)),
      );
      state.options = await api("/api/options");
      closeModal();
      backupNotice(result);
      await render();
    } catch (error) {
      $(".form-error", e.target).textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}
async function rulesPage() {
  const rules = await api("/api/rules");
  $("#content").innerHTML =
    header("分类规则", "让软件记住具体用途，减少重复选择。") +
    `<p class="page-intro">在记账或修改类目时勾选“记住这个用途关键词”，规则会保存在本机。用途不明确时仍需核对。</p><section class="panel"><div class="panel-head"><h2>已学习的用途关键词</h2><small>${rules.length} 条规则</small></div><div class="table-wrap"><table><thead><tr><th>具体关键词</th><th>归入类目</th><th>操作</th></tr></thead><tbody>${rules.length ? rules.map((rule) => `<tr><td>${esc(rule.keyword)}</td><td><span class="tag">${esc(rule.category_name)}</span></td><td><button class="small quiet danger" data-rule="${rule.id}">移除</button></td></tr>`).join("") : '<tr><td colspan="3" class="empty-row">还没有学习规则。下一笔记账时，可以勾选保存具体用途的分类。</td></tr>'}</tbody></table></div></section>`;
  $$("[data-rule]").forEach(
    (el) =>
      (el.onclick = async () => {
        if (!confirm("确认移除这个关键词规则？已经登记的账目不会改变。"))
          return;
        try {
          backupNotice(
            await api(`/api/rules/${el.dataset.rule}`, {}),
            "规则已移除并备份",
          );
          await render();
        } catch (error) {
          toast(error.message, true);
        }
      }),
  );
}
async function settingsPage() {
  const info = await api("/api/backup-status");
  const complete = info.record_backup_count === info.event_count;
  $("#content").innerHTML =
    header("备份与设置", "每一笔账目和库存变动，都会留下可追溯的记录。") +
    `<div class="settings-grid"><section class="panel"><div class="panel-head"><h2>数据备份</h2><span class="tag ${complete ? "good" : "warn"}">${complete ? "逐笔备份已同步" : "备份待修复"}</span></div><div class="panel-body"><div class="backup-hero">${icon("shield")}<div><strong>给账本多留一份底</strong><p>下载包包含数据库、原始截图和逐笔变更记录。建议定期保存到另一块磁盘。</p><a href="/api/backup.zip"><button class="primary small">${icon("down")} 下载完整备份</button></a></div></div><div class="info-metrics"><div><b>${info.record_backup_count}</b><span>逐笔变更备份</span></div><div><b>${info.receipt_backup_count}</b><span>原始凭证备份</span></div></div><div class="setting-row"><div><strong>数据保存位置</strong><p><code>${esc(info.data_directory)}</code></p></div></div><div class="setting-row"><div><strong>重新核对备份</strong><p>根据已保存的变更补齐缺失备份，不会重复记账。</p></div><button class="small" id="repair-backup">核对并备份</button></div><div class="setting-row"><div><strong>账目导出</strong><p>适合用 Excel 查看；完整恢复请下载完整备份。</p></div><a href="/api/export.csv"><button class="small">导出 CSV</button></a></div></div></section><section class="panel"><div class="panel-head"><h2>账本设置</h2><small>版本 ${esc(info.version)}</small></div><div class="panel-body"><div class="setting-row"><div><strong>截图识别</strong><p>${info.ocr_available ? "本地识别组件已安装。首次使用需准备模型。" : "识别组件尚未安装。可保存截图并手工填写，或按安装说明启用。"}</p></div><span class="tag ${info.ocr_available ? "good" : "warn"}">${info.ocr_available ? "可用" : "未启用"}</span></div><div class="setting-row"><div><strong>退出此设备</strong><p>清除当前登录，账目和备份继续保留。</p></div><button class="small" id="logout-device">退出登录</button></div><div class="setting-row"><div><strong>手机与电脑连接</strong><p>${esc(info.access_mode)}</p></div></div><div class="setting-row"><div><strong>付款与收款账户</strong><p>${state.options.accounts.map((a) => esc(a.name)).join("、")}</p></div><button class="small" id="new-account">新增</button></div><div class="setting-row"><div><strong>收支类目</strong><p>按店铺经营方式增加类目，不影响历史账目。</p></div><button class="small" id="new-category">新增</button></div><div class="setting-row"><div><strong>小票店铺信息</strong><p>名称、地址和联系电话会打印在销售小票上。</p></div><button class="small" id="shop-settings">设置</button></div><div class="setting-row"><div><strong>用途分类规则</strong><p>查看或移除已经学习的关键词。</p></div><button class="small" id="go-rules">管理</button></div></div></section></div><p class="footer-note">账本不自动连接微信、支付宝或银行账户；以导入凭证和人工确认的数据为准。</p>`;
  $("#repair-backup").onclick = async () => {
    try {
      backupNotice(await api("/api/backup", {}), "逐笔备份与数据库快照已更新");
      await render();
    } catch (error) {
      toast(error.message, true);
    }
  };
  $("#shop-settings").onclick = shopModal;
  if (state.authMode === "direct") {
    $("#logout-device").closest(".setting-row").remove();
  } else {
    $("#logout-device").onclick = async () => {
      await api("/api/logout", {});
      await boot();
    };
  }
  $("#go-rules").onclick = () => navigate("rules");
  $("#new-account").onclick = () => {
    modal(
      "新增收支账户",
      `<form id="account-form"><label class="form-field"><span>账户名称</span><input name="name" required maxlength="80" placeholder="例如：常用储蓄卡(1234)"></label><p class="note">这里管理收支来源，不要求提供账号、密码或支付授权。</p><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">新增并备份</button></div></form>`,
    );
    bindSimpleForm("#account-form", "/api/accounts");
  };
  $("#new-category").onclick = () => {
    modal(
      "新增收支类目",
      `<form id="category-form"><div class="form-grid"><label class="form-field"><span>方向</span><select name="kind"><option value="expense">支出</option><option value="income">收入</option></select></label><label class="form-field"><span>类目名称</span><input name="name" required maxlength="80" placeholder="例如：设备维修"></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">新增并备份</button></div></form>`,
    );
    bindSimpleForm("#category-form", "/api/categories");
  };
}
document.addEventListener("change", async (e) => {
  if (e.target.matches("input[name=occurred_at]")) {
    const precision = $("[name=time_precision]", e.target.form);
    if (precision)
      precision.value = /T\d{2}:\d{2}:\d{2}$/.test(e.target.value)
        ? "second"
        : "minute";
  }
  if (e.target.matches(".draft-kind")) {
    const form = e.target.form;
    const value = Object.fromEntries(new FormData(form));
    const previous = form.dataset.kind;
    form.reviewKinds[previous] = {};
    for (const key of ["category_id", "to_account_id", "refund_of", "learn"]) {
      if (key in value) form.reviewKinds[previous][key] = value[key];
      value[key] = "";
    }
    Object.assign(value, form.reviewKinds[e.target.value]);
    $(".draft-fields", form).innerHTML = transactionFields(
      e.target.value,
      value,
    );
    if (value.learn === "yes" && $("[name=learn]", form))
      $("[name=learn]", form).checked = true;
    form.dataset.kind = e.target.value;
    accessibleForms(form);
  }
  if (e.target.matches("input[name=purpose]")) {
    const form = e.target.form;
    const select = $("[name=category_id]", form);
    if (!select) return;
    const purpose = e.target.value,
      category = select.value;
    try {
      const rules = await api("/api/rules");
      const match = rules.find(
        (rule) => rule.field === "purpose" && purpose.includes(rule.keyword),
      );
      if (
        form.isConnected &&
        e.target.value === purpose &&
        select.value === category &&
        match &&
        [...select.options].some((o) => o.value === match.category_id)
      )
        select.value = match.category_id;
      if (form.matches("[data-draft]") && form.isConnected)
        cacheReviewForm(form);
    } catch (error) {
      toast(error.message, true);
    }
  }
  if (e.target.form?.matches("[data-draft]")) cacheReviewForm(e.target.form);
});
document.addEventListener("input", (e) => {
  if (e.target.form?.matches("[data-draft]")) cacheReviewForm(e.target.form);
});
window.addEventListener("beforeunload", (e) => {
  if (state.reviewEdits.size || modalContext?.pending) {
    e.preventDefault();
    e.returnValue = "";
  }
});
document.addEventListener("keydown", (e) => {
  const dialog = $(".modal");
  if (!dialog) return;
  if (e.key === "Escape") {
    e.preventDefault();
    requestCloseModal();
  }
  if (e.key === "Tab") {
    const controls = $$(
      "button:not(:disabled),input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),a[href]",
      dialog,
    ).filter((el) => !el.closest("[inert]") && el.getClientRects().length);
    const first = controls[0],
      last = controls.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
});
boot();
