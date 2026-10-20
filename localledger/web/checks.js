"use strict";
async function dailyCheckModal(day = dateInput().slice(0, 10), scope = "all") {
  const result = await api(
    "/api/daily-check?day=" +
      encodeURIComponent(day) +
      "&scope=" +
      encodeURIComponent(scope),
  );
  modal(
    "每日总额核对",
    `<div class="notice">这里保存的是核对总额，不会新增收入或支出。请按同一天、同一账户的净额核对，退款已冲减，内部转账不计入。</div><div class="form-grid"><label class="form-field"><span>核对日期</span><input id="check-day" type="date" value="${esc(day)}" required></label><label class="form-field"><span>核对账户</span><select id="check-scope"><option value="all">全部账户合计</option>${accountOptions(scope)}</select></label></div><p class="note">本范围 ${result.transaction_count} 笔有效收支；记账或退款变化后会重新计算差额。</p>${result.checks.map((row) => `<section class="check-section"><div class="panel-head"><h3>${row.kind === "income" ? "净收入" : "净支出"}</h3><span class="tag ${row.matched === true ? "good" : "warn"}">${row.matched === true ? "已对平" : row.matched === false ? "存在差额" : "待填写核对总额"}</span></div><p>账本合计 <strong>¥${cash(row.actual_cents)}</strong>${row.difference_cents !== null ? " · 账本减核对额 <strong>¥" + cash(row.difference_cents) + "</strong>" : ""}</p><form data-check-form="${row.kind}"><div class="form-grid"><label class="form-field"><span>${row.kind === "income" ? "净收入核对总额（元）" : "净支出核对总额（元）"}</span><input name="amount" type="number" step="0.01" required value="${row.expected_cents !== null ? (row.expected_cents / 100).toFixed(2) : ""}"></label><label class="form-field"><span>核对说明</span><input name="note" maxlength="500" value="${esc(row.note)}"></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button class="primary small" type="submit">保存${row.kind === "income" ? "收入" : "支出"}核对额</button></div></form></section>`).join("")}`,
    true,
  );
  $("#check-scope").value = scope;
  $("#check-day").onchange = () => {
    if (!allowChange()) return;
    if ($("#check-day").value)
      dailyCheckModal($("#check-day").value, $("#check-scope").value);
  };
  $("#check-scope").onchange = () => {
    if (allowChange())
      dailyCheckModal($("#check-day").value, $("#check-scope").value);
  };
  function allowChange() {
    if ($(".modal button[type=submit]:disabled")) {
      $("#check-day").value = day;
      $("#check-scope").value = scope;
      toast("正在保存，请等待结果后切换核对范围");
      return false;
    }
    if (
      modalValues() !== modalContext.initial &&
      !confirm("核对总额尚未保存，确定放弃并切换日期或账户？")
    ) {
      $("#check-day").value = day;
      $("#check-scope").value = scope;
      return false;
    }
    modalContext.edited = false;
    modalContext.initial = modalValues();
    return true;
  }
  $$("[data-check-form]").forEach((form) => {
    const row = result.checks.find((x) => x.kind === form.dataset.checkForm);
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = $("button", form);
      button.disabled = true;
      try {
        const saved = await api("/api/daily-check", {
          ...formData(form),
          day,
          scope,
          kind: row.kind,
          version: row.version,
        });
        backupNotice(saved, "已保存核对总额，没有重复入账");
        modalContext.edited = false;
        modalContext.initial = modalValues();
        await dailyCheckModal(day, scope);
      } catch (error) {
        $(".form-error", form).textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  });
}
