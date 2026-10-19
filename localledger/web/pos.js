"use strict";
const pos = { menu: [], cart: [], key: null, pending: null, ledger: null };
function restorePos() {
  if (pos.ledger === state.ledgerId) return;
  pos.ledger = state.ledgerId;
  pos.cart = [];
  pos.key = null;
  pos.pending = null;
  try {
    const value = JSON.parse(
      localStorage.getItem("ledger-pos-" + pos.ledger) || "null",
    );
    if (!value) return;
    if (
      !Array.isArray(value.cart) ||
      value.cart.length > 100 ||
      value.cart.some(
        (x) =>
          !x ||
          typeof x.menu_id !== "string" ||
          typeof x.name !== "string" ||
          !Number.isSafeInteger(x.expected_price_cents) ||
          !Number.isSafeInteger(x.quantity) ||
          x.quantity < 1 ||
          x.quantity > 999,
      )
    )
      throw new Error();
    pos.cart = value.cart;
    pos.key = value.key || null;
    pos.pending = value.pending || null;
  } catch {
    toast("本机收银草稿无法恢复，请先核对最近销售小票，避免重复开单", true);
  }
}
function persistPos(required = false) {
  try {
    const key = "ledger-pos-" + pos.ledger;
    if (pos.cart.length || pos.pending)
      localStorage.setItem(
        key,
        JSON.stringify({ cart: pos.cart, key: pos.key, pending: pos.pending }),
      );
    else localStorage.removeItem(key);
    return true;
  } catch {
    toast(
      required
        ? "无法保存收银请求，请允许浏览器本地存储后重试；本次尚未提交"
        : "本机无法保存购物车草稿，请保持页面并及时核对销售小票",
      true,
    );
    return false;
  }
}
async function completeSale(result) {
  pos.cart = [];
  pos.key = null;
  pos.pending = null;
  persistPos();
  closeModal();
  backupNotice(result, "销售与原料流水已保存");
  await showTicket(result.id);
  await render();
}
async function recoverPendingSale() {
  if (!pos.pending) return;
  modal(
    "核实上次收银结果",
    '<div class="notice">上次请求未收到确定结果。请勿重新收款。继续使用原编号核实，若已保存则返回原小票。</div><div class="form-error" role="alert"></div><button id="recover-sale" class="primary full">核实原请求并显示小票</button>',
  );
  $("#recover-sale").onclick = async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await completeSale(await api("/api/checkout", pos.pending));
    } catch (error) {
      $(".form-error", $(".modal")).textContent = error.message;
      if (error.status === 400) {
        pos.pending = null;
        persistPos();
        closeModal();
        toast("本单未保存：请修正问题后使用原购物车重试", true);
        await render();
      }
    } finally {
      button.disabled = false;
    }
  };
}
async function posPage() {
  restorePos();
  [pos.menu, state.products] = await Promise.all([
    api("/api/menu"),
    api("/api/products"),
  ]);
  $("#content").innerHTML =
    header(
      "餐饮收银",
      "菜品销售、原料扣减和收款账目一起保存",
      '<button id="menu-settings">菜品与配方</button><button id="sales-history">销售小票</button>',
    ) +
    `<div class="notice">付款码自动扣款：待开通商户接口。当前只能核实实收后记账，软件不会向顾客发起扣款。</div><p class="note">本浏览器暂存购物车和待核实请求。请勿在多个标签同时处理同一单；清除浏览器数据会丢弃未完成草稿。</p><div class="pos-layout"><section class="panel"><div class="panel-body"><form id="barcode-form" class="scan-form"><label class="form-field"><span>扫描菜品条码 / 输入菜品编码</span><input id="barcode" placeholder="扫码枪输入后回车" autocomplete="off" maxlength="100"></label><button type="submit">加入</button></form><div class="menu-grid" id="menu-grid"></div></div></section><section class="panel pos-cart"><div class="panel-head"><h2>本单明细</h2><button id="clear-cart" class="small">清空</button></div><div class="panel-body" id="cart-body"></div></section></div>`;
  $("#menu-settings").onclick = menuSettings;
  $("#sales-history").onclick = () => navigate("orders");
  $("#clear-cart").onclick = () => {
    if (pos.pending) return toast("上次收银结果待核实，请先核实原请求", true);
    if (!pos.cart.length || confirm("清空当前购物车？尚未保存的明细将丢弃。")) {
      pos.cart = [];
      pos.key = null;
      persistPos();
      drawCart();
    }
  };
  $("#barcode-form").onsubmit = (e) => {
    e.preventDefault();
    const code = $("#barcode").value.trim();
    const item = pos.menu.find((x) => x.barcode === code && x.active);
    if (!item) return toast("没有找到此编码，请在菜品与配方中新增", true);
    addCart(item.id);
    $("#barcode").value = "";
  };
  $("#menu-grid").innerHTML =
    pos.menu
      .filter((x) => x.active)
      .map(
        (x) =>
          `<button class="menu-card" data-dish="${x.id}"><strong>${esc(x.name)}</strong><span>¥${cash(x.price_cents)}</span><small>${esc(x.barcode)} · ${x.stock_mode === "recipe" ? "配方扣库" : "手工领料"}</small></button>`,
      )
      .join("") ||
    '<div class="empty-row">先添加菜品、售价和配方，即可开始收银。</div>';
  $$("[data-dish]").forEach((b) => (b.onclick = () => addCart(b.dataset.dish)));
  drawCart();
}
function addCart(key) {
  if (pos.pending) return toast("上次收银结果待核实，请先核实原请求", true);
  const item = pos.menu.find((x) => x.id === key);
  const old = pos.cart.find((x) => x.menu_id === key);
  if (old && old.quantity >= 999) return toast("每行最多999份", true);
  if (old) old.quantity++;
  else
    pos.cart.push({
      menu_id: key,
      name: item.name,
      quantity: 1,
      expected_price_cents: item.price_cents,
      stock_mode: item.stock_mode,
    });
  pos.key = null;
  persistPos();
  drawCart();
}
function cartTotal() {
  return pos.cart.reduce(
    (sum, x) => sum + x.expected_price_cents * x.quantity,
    0,
  );
}
function drawCart() {
  const body = $("#cart-body");
  if (!body) return;
  body.innerHTML =
    pos.cart
      .map(
        (x) =>
          `<div class="cart-line"><div><strong>${esc(x.name)}</strong><small>¥${cash(x.expected_price_cents)} / 份${x.stock_mode === "none" ? " · 请另行领料" : ""}</small></div><div class="qty-control"><button data-minus="${x.menu_id}" aria-label="减少${esc(x.name)}">−</button><span>${x.quantity}</span><button data-plus="${x.menu_id}" aria-label="增加${esc(x.name)}">+</button></div></div>`,
      )
      .join("") +
    (!pos.cart.length
      ? '<p class="empty-row">点选左侧菜品或扫描菜品编码</p>'
      : "") +
    `<div class="cart-total"><span>应收合计</span><strong>¥${cash(cartTotal())}</strong></div><button class="primary full" id="checkout-button" ${pos.cart.length ? "" : "disabled"}>${pos.pending ? "核实上次收银结果" : "核实收款并开单"}</button>`;
  $$("[data-minus]").forEach(
    (b) =>
      (b.onclick = () => {
        if (pos.pending) return toast("请先核实上次收银结果", true);
        const line = pos.cart.find((x) => x.menu_id === b.dataset.minus);
        line.quantity--;
        pos.cart = pos.cart.filter((x) => x.quantity);
        pos.key = null;
        persistPos();
        drawCart();
      }),
  );
  $$("[data-plus]").forEach((b) => (b.onclick = () => addCart(b.dataset.plus)));
  $("#checkout-button").onclick = checkoutModal;
}
function checkoutModal() {
  if (pos.pending) return recoverPendingSale();
  pos.key = pos.key || uid();
  persistPos();
  const total = cartTotal();
  modal(
    "核实收款",
    `<div class="detail-amount">¥${cash(total)}</div><div class="notice">当前未接入付款码扣款。请通过店铺现有收款方式收款，并核实实际到账；不要在这里输入顾客付款码。</div><form id="checkout-form"><div class="form-grid"><label class="form-field"><span>收款方式</span><select name="payment_method"><option value="cash">现金</option><option value="wechat">微信</option><option value="alipay">支付宝</option><option value="other">其他</option></select></label><label class="form-field"><span>记入账户</span><select name="account_id">${accountOptions("cash")}</select></label><label class="form-field wide"><span>收款时间</span><input type="datetime-local" name="occurred_at" value="${dateInput()}" required></label><label class="form-field wide"><span>收款流水号（有则填写）</span><input name="payment_reference" maxlength="200"></label><label class="form-field wide"><span>桌号 / 备注</span><input name="note" maxlength="500"></label><label class="check-line form-field wide"><span><input type="checkbox" name="payment_confirmed" required> 已核实收到 ¥${cash(total)}，确认记账并扣减配方原料</span></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit" class="primary">保存销售并生成小票</button></div></form>`,
    true,
  );
  const form = $("#checkout-form");
  $("[name=payment_method]", form).onchange = (e) => {
    const match = state.options.accounts.find((x) => x.id === e.target.value);
    if (match) $("[name=account_id]", form).value = match.id;
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const b = $("button[type=submit]", form);
    b.disabled = true;
    try {
      const data = formData(form);
      data.payment_confirmed = $("[name=payment_confirmed]", form).checked;
      pos.pending = pos.pending || {
        ...data,
        items: JSON.parse(JSON.stringify(pos.cart)),
        expected_total_cents: total,
        idempotency_key: pos.key,
      };
      if (!persistPos(true)) {
        pos.pending = null;
        return;
      }
      const result = await api("/api/checkout", pos.pending);
      await completeSale(result);
    } catch (error) {
      if (error.status === 400) {
        pos.pending = null;
        persistPos();
      }
      drawCart();
      $(".form-error", form).textContent =
        error.message +
        (pos.pending
          ? "；收银结果待核实，请勿重新收款。可关闭弹窗后核实原请求。"
          : "");
    } finally {
      b.disabled = false;
    }
  };
}
async function menuSettings() {
  pos.menu = await api("/api/menu");
  modal(
    "菜品与配方",
    `<div class="form-footer"><button id="new-dish" class="primary small">新增菜品</button></div><div class="table-wrap"><table><thead><tr><th>菜品 / 编码</th><th>售价</th><th>领料方式</th><th>操作</th></tr></thead><tbody>${pos.menu.map((x) => `<tr><td>${esc(x.name)}${x.active ? "" : "（停用）"}<small class="secondary">${esc(x.barcode)}</small></td><td>¥${cash(x.price_cents)}</td><td>${x.stock_mode === "recipe" ? `${x.recipe.length}种原料` : "手工领料"}</td><td><button data-edit-dish="${x.id}" class="small">编辑</button></td></tr>`).join("") || '<tr><td colspan="4">暂无菜品</td></tr>'}</tbody></table></div><p class="note">配方数量采用原料库存的单位。例如原料单位为千克，500克填写0.5。历史销售保留当时的售价与配方。</p>`,
    true,
  );
  $("#new-dish").onclick = () => dishModal();
  $$("[data-edit-dish]").forEach(
    (b) =>
      (b.onclick = () =>
        dishModal(pos.menu.find((x) => x.id === b.dataset.editDish))),
  );
}
function dishModal(item = {}) {
  const recipe = item.recipe || [];
  modal(
    item.id ? "编辑菜品" : "新增菜品",
    `<form id="dish-form"><div class="form-grid"><label class="form-field"><span>菜品名称</span><input name="name" required maxlength="100" value="${esc(item.name || "")}"></label><label class="form-field"><span>菜品条码 / 编码</span><input name="barcode" required maxlength="100" value="${esc(item.barcode || "")}"></label><label class="form-field"><span>每份售价（元）</span><input name="price" type="number" min="0.01" step="0.01" required value="${item.price_cents ? (item.price_cents / 100).toFixed(2) : ""}"></label><label class="form-field"><span>原料扣减</span><select name="stock_mode"><option value="recipe" ${item.stock_mode !== "none" ? "selected" : ""}>按每份配方自动扣库</option><option value="none" ${item.stock_mode === "none" ? "selected" : ""}>手工领料，不自动扣库</option></select></label><label class="check-line form-field wide"><span><input type="checkbox" name="active" ${item.active === 0 ? "" : "checked"}> 在售</span></label></div><div id="recipe-editor"><h3>每份消耗原料</h3><p class="note">只填写实际配方，不确定时选择手工领料；单位与库存保持一致。</p><div id="recipe-lines"></div><button type="button" class="small" id="add-recipe">添加原料</button></div><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">保存菜品与配方</button></div></form>`,
    true,
  );
  const add = (line = {}) => {
    const row = document.createElement("div");
    row.className = "recipe-line";
    row.innerHTML = `<label class="form-field"><span>原料及库存单位</span><select name="product_id" required>${state.products.map((p) => `<option value="${p.id}" ${line.product_id === p.id ? "selected" : ""}>${esc(p.name)}（${esc(p.unit)}）</option>`).join("")}</select></label><label class="form-field"><span>每份用量</span><input name="quantity" type="number" min="0.001" step="0.001" required value="${line.quantity_milli ? line.quantity_milli / 1000 : ""}"></label><button type="button" class="small" aria-label="移除此原料">移除</button>`;
    $("button", row).onclick = () => row.remove();
    $("#recipe-lines").append(row);
  };
  recipe.forEach(add);
  if (!recipe.length && state.products.length) add();
  $("#add-recipe").onclick = () =>
    state.products.length ? add() : toast("请先在库存管理中添加原料", true);
  const form = $("#dish-form");
  const toggle = () => {
    const off = $("[name=stock_mode]", form).value === "none";
    $("#recipe-editor").hidden = off;
    $$("#recipe-editor input, #recipe-editor select").forEach(
      (el) => (el.disabled = off),
    );
  };
  $("[name=stock_mode]", form).onchange = toggle;
  toggle();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const b = $("button[type=submit]", form);
    b.disabled = true;
    try {
      const data = formData(form);
      data.active = $("[name=active]", form).checked;
      data.recipe =
        data.stock_mode === "recipe"
          ? $$(".recipe-line", form).map((row) => ({
              product_id: $("[name=product_id]", row).value,
              quantity: $("[name=quantity]", row).value,
            }))
          : [];
      const result = await api(
        "/api/menu" + (item.id ? "/" + item.id : ""),
        data,
      );
      closeModal();
      backupNotice(result);
      await posPage();
      await menuSettings();
    } catch (error) {
      $(".form-error", form).textContent = error.message;
    } finally {
      b.disabled = false;
    }
  };
}
async function ordersPage() {
  const orders = await api("/api/orders?month=" + state.month);
  $("#content").innerHTML =
    header(
      "销售小票",
      "保留当时的菜品、售价、数量与原料配方",
      monthPicker() + '<button id="go-pos" class="primary">开始收银</button>',
    ) +
    `<section class="panel"><div class="table-wrap"><table><thead><tr><th>小票编号 / 时间</th><th>备注</th><th>状态</th><th>金额</th><th>操作</th></tr></thead><tbody>${orders.map((o) => `<tr><td>${esc(o.number)}<small class="secondary">${esc(o.occurred_at)}</small></td><td>${esc(o.note)}</td><td><span class="tag">${o.status === "paid" ? "已收款·人工核实" : "已退单"}</span></td><td>¥${cash(o.total_cents)}</td><td><button data-ticket="${o.id}" class="small">查看小票</button>${o.status === "paid" ? `<button data-return-order="${o.id}" class="small danger">退单</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="5" class="empty-row">本月尚无销售小票</td></tr>'}</tbody></table></div></section>`;
  $("#go-pos").onclick = () => navigate("pos");
  $$("[data-ticket]").forEach(
    (b) => (b.onclick = () => showTicket(b.dataset.ticket)),
  );
  $$("[data-return-order]").forEach(
    (b) => (b.onclick = () => refundSale(b.dataset.returnOrder)),
  );
}
async function showTicket(key) {
  const order = await api("/api/orders/" + key);
  const ticket = `<article class="sale-ticket"><h2>${esc(order.shop.name)}</h2>${order.shop.address ? `<p>${esc(order.shop.address)}</p>` : ""}${order.shop.phone ? `<p>电话：${esc(order.shop.phone)}</p>` : ""}<h3>销售小票${order.status === "refunded" ? "（已退单）" : ""}</h3><p>单号：${esc(order.number)}<br>时间：${esc(order.occurred_at)}</p><table><thead><tr><th>菜品</th><th>数量</th><th>单价</th><th>金额</th></tr></thead><tbody>${order.lines.map((l) => `<tr><td>${esc(l.name)}</td><td>${l.quantity}</td><td>${cash(l.price_cents)}</td><td>${cash(l.price_cents * l.quantity)}</td></tr>`).join("")}</tbody></table><div class="ticket-total">合计 ¥${cash(order.total_cents)}</div><p>收款方式：${{ cash: "现金", wechat: "微信", alipay: "支付宝", other: "其他" }[order.payment_method]}<br>收款核实：店员人工确认${order.payment_reference ? "<br>流水号：" + esc(order.payment_reference) : ""}</p>${order.note ? `<p>备注：${esc(order.note)}</p>` : ""}${order.refund ? `<p>退单时间：${esc(order.refund.occurred_at)}<br>原因：${esc(order.refund.reason)}</p>` : ""}<p>${esc(order.shop.footer)}</p><small>本小票为销售凭证，不是税务发票。</small></article>`;
  modal(
    "销售小票",
    `<div class="ticket-tools"><label>打印宽度 <select id="ticket-width"><option value="80">80毫米</option><option value="58">58毫米</option></select></label><button id="print-ticket" class="primary">打印 / 保存 PDF</button></div><div id="print-area">${ticket}</div>`,
  );
  $("#print-ticket").onclick = () => {
    $("#print-area").dataset.width = $("#ticket-width").value;
    let style = $("#ticket-page-style");
    if (!style) {
      style = document.createElement("style");
      style.id = "ticket-page-style";
      document.head.append(style);
    }
    // Browsers may require selecting the same paper width in their print dialog.
    style.textContent =
      "@media print { @page { size: " +
      $("#ticket-width").value +
      "mm auto; margin: 3mm; } }";
    window.print();
  };
}
async function refundSale(key) {
  const order = await api("/api/orders/" + key);
  const idem = uid();
  modal(
    "整单退款",
    `<p>退还小票 ${esc(order.number)} 的全部金额：<strong>¥${cash(order.total_cents)}</strong></p><div class="notice">退款冲减销售收入。食品原料不会自动回库；可继续使用的实物请另行盘点。当前仅支持整单退，不支持部分退菜。</div><form id="sale-refund-form"><label class="form-field"><span>退款时间</span><input name="occurred_at" type="datetime-local" required value="${dateInput()}"></label><label class="form-field"><span>实际退款账户</span><select name="account_id">${accountOptions(order.account_id)}</select></label><label class="form-field"><span>退款原因</span><input name="reason" required maxlength="500"></label><label class="check-line form-field"><span><input type="checkbox" name="payment_confirmed" required> 已通过原收款方式退还款项，确认登记退单</span></label><div class="form-error" role="alert"></div><button class="danger" type="submit">登记退款与备份</button></form>`,
  );
  const form = $("#sale-refund-form");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const b = $("button", form);
    b.disabled = true;
    try {
      const data = formData(form);
      data.payment_confirmed = $("[name=payment_confirmed]", form).checked;
      const result = await api("/api/orders/" + key + "/refund", {
        ...data,
        idempotency_key: idem,
      });
      closeModal();
      backupNotice(result, "退单已登记，未自动回库");
      await render();
    } catch (error) {
      $(".form-error", form).textContent = error.message;
    } finally {
      b.disabled = false;
    }
  };
}
async function shopModal() {
  const data = await api("/api/shop");
  modal(
    "小票店铺信息",
    `<form id="shop-form">${[
      ["name", "店铺名称"],
      ["address", "地址"],
      ["phone", "联系电话"],
      ["footer", "小票结尾"],
    ]
      .map(
        ([k, label]) =>
          `<label class="form-field"><span>${label}</span><input name="${k}" maxlength="150" ${k === "name" ? "required" : ""} value="${esc(data[k])}"></label>`,
      )
      .join(
        "",
      )}<div class="form-error" role="alert"></div><div class="form-footer"><button class="primary" type="submit">保存</button></div></form>`,
  );
  bindSimpleForm("#shop-form", "/api/shop");
}
