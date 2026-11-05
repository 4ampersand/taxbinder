// The binder's pages (Documents, Payments, Donations, Foreign accounts, Changes, Check, Settings,
// …): portal windows, uploads, verify, re-run checks. page.html loads it after app.js, whose
// viewer opens anything marked data-view. No inline scripts (CSP script-src 'self') and no
// browser dialogs (they block the desktop shell).
(function () {
  const $ = (id) => document.getElementById(id);
  // Messages: the page's own line where it has one (pairing page), else a toast.
  let toastTimer = null;
  const msg = (text, cls) => {
    const line = $("msg");
    if (line) { line.className = cls || "muted"; line.textContent = text; return; }
    const t = $("toast");
    if (!t) return;
    if (!text) { t.hidden = true; return; }
    if (cls === "ok" && !t.hidden && t.querySelector("button.undo")) return;  // keep the Undo offer
    t.textContent = text; t.className = "toast" + (cls === "bad" ? " bad" : ""); t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, cls === "bad" ? 8000 : 4500);
  };
  // Every change the server reports ("undo": the change it made) is offered for undo in a toast,
  // kept for a few seconds across the page reload most actions do.
  const UNDO_KEY = "tb.undo";
  function offerUndo(undo) {
    try { sessionStorage.setItem(UNDO_KEY, JSON.stringify({ ...undo, at: Date.now() })); } catch (e) { /* ignore */ }
    showUndo(undo);
  }
  function showUndo(undo) {
    const t = $("toast");
    if (!t) return;
    t.textContent = "Done: " + undo.message.replace(/ \[[0-9a-f]{12}\]$/, "") + " ";
    const b = document.createElement("button"); b.type = "button"; b.className = "undo"; b.textContent = "Undo";
    b.addEventListener("click", async () => {
      b.disabled = true;
      try { sessionStorage.removeItem(UNDO_KEY); } catch (e) { /* ignore */ }
      const r = await post("/api/undo", { sha: undo.sha });
      if (r.ok) reload(); else msg(await errorOf(r), "bad");
    });
    t.appendChild(b); t.className = "toast"; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 10000);
  }
  const post = async (path, body) => {
    const r = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
    });
    if (r.ok) r.clone().json().then((j) => { if (j && j.undo) offerUndo(j.undo); }).catch(() => {});
    return r;
  };
  const errorOf = async (r) => ((await r.json().catch(() => ({}))).error || "Error " + r.status);
  // A note answered with one click
  document.querySelectorAll("[data-notice]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await post("/api/notice", { id: b.dataset.notice, action: b.dataset.action });
    if (r.ok) reload(); else { msg(await errorOf(r), "bad"); b.disabled = false; }
  }));

  // Reload but keep the scroll position (after an action changes the page's data).
  const SCROLL = "tp.scroll." + location.pathname;
  function reload() {
    // the window's position and each table's own (tables scroll inside their box)
    const tables = [...document.querySelectorAll(".table-wrap")].map((w) => [w.scrollTop, w.scrollLeft]);
    try { sessionStorage.setItem(SCROLL, JSON.stringify({ y: window.scrollY, tables })); } catch (e) { /* ignore */ }
    const u = new URL(location.href);  // the server remembers the year; a stale ?year= would undo
    u.searchParams.delete("year");     // a switch made by the action (e.g. Start 2026)
    location.replace(u.toString());
  }
  try {
    const saved = sessionStorage.getItem(SCROLL);
    if (saved !== null) {
      sessionStorage.removeItem(SCROLL);
      const at = JSON.parse(saved);
      const restore = () => {
        window.scrollTo(0, at.y || 0);
        document.querySelectorAll(".table-wrap").forEach((w, i) => {
          const t = (at.tables || [])[i];
          if (t) { w.scrollTop = t[0]; w.scrollLeft = t[1]; }
        });
      };
      restore(); requestAnimationFrame(restore);  // again once the grid has laid out its rows
    }
  } catch (e) { /* ignore */ }

  // Row menus (•••) and dialogs: one open at a time; Escape or a click elsewhere closes them.
  const closeMenus = () => document.querySelectorAll(".row-menu").forEach((b) => {
    const m = b.nextElementSibling;
    b.setAttribute("aria-expanded", "false"); m.hidden = true;
    if (m.matches("[popover]:popover-open")) m.hidePopover();
  });
  document.querySelectorAll(".row-menu").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = b.nextElementSibling.hidden;
    closeMenus();
    b.nextElementSibling.hidden = !open; b.setAttribute("aria-expanded", String(open));
    const m = b.nextElementSibling;
    if (open && b.closest(".table-wrap")) {  // place it on the window so the table's edge doesn't cut it off
      // on the top layer (a popover) where it can: no table edge or scroll box can cut it off
      m.classList.add("floating");
      if (m.showPopover) { m.setAttribute("popover", "manual"); m.showPopover(); }
      const r = b.getBoundingClientRect(), h = m.offsetHeight;
      m.style.right = (window.innerWidth - r.right) + "px";
      m.style.top = (r.bottom + 6 + h > window.innerHeight ? Math.max(8, r.top - 6 - h) : r.bottom + 6) + "px";
    }
  }));
  document.addEventListener("scroll", (e) => { if (!e.target.closest || !e.target.closest(".menu")) closeMenus(); }, true);
  // a click inside a menu stays in it, but View goes on to the viewer (app.js), closing the menu
  document.querySelectorAll(".acts .menu").forEach((m) => m.addEventListener("click", (e) => {
    if (!e.target.closest("[data-view]")) e.stopPropagation();
  }));
  document.addEventListener("click", closeMenus);
  // A dialog keeps the keyboard inside it (Tab and Shift-Tab go round), and on closing puts
  // focus back on what opened it.
  let opener = null;
  const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
  const inside = (d) => [...d.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
  const openDialog = (id) => {
    opener = document.activeElement;
    $(id).hidden = false;
    const f = $(id).querySelector("input, select") || inside($(id))[0];
    if (f) f.focus();
  };
  const closeDialogs = () => {
    let was = false;
    document.querySelectorAll(".scrim").forEach((d) => { if (!d.hidden) was = true; d.hidden = true; });
    if (was && opener && document.contains(opener)) opener.focus();
    if (was) opener = null;
  };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const d = [...document.querySelectorAll(".scrim")].find((x) => !x.hidden && x.id !== "viewer");  // the viewer sits beside the page
    if (!d) return;
    const els = inside(d);
    if (!els.length) { e.preventDefault(); return; }
    const first = els[0], last = els[els.length - 1];
    if (!d.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeDialogs));
  document.querySelectorAll(".scrim").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) closeDialogs(); }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDialogs(); closeMenus(); } });

  try {  // an undo offer from just before this page loaded
    const pending = JSON.parse(sessionStorage.getItem(UNDO_KEY) || "null");
    if (pending && Date.now() - pending.at < 10000) setTimeout(() => showUndo(pending), 50);
    else sessionStorage.removeItem(UNDO_KEY);
  } catch (e) { /* ignore */ }
  document.querySelectorAll(".act-undo").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await post("/api/undo", { sha: b.closest("tr").dataset.sha });
    if (r.ok) reload(); else { msg(await errorOf(r), "bad"); b.disabled = false; }
  }));

  // Files dropped outside a drop zone must not replace the page with the file.
  ["dragover", "drop"].forEach((t) => window.addEventListener(t, (e) => e.preventDefault()));

  // -- portal (Documents page)
  async function openPortal() {
    const url = $("url").value.trim();
    msg("Opening…");
    const r = await post("/api/open-portal", { url });
    if (r.ok) msg("Portal window requested. Downloads made there are added automatically.", "ok");
    else msg(await errorOf(r), "bad");
  }

  async function savePage() {
    const count = async () => (await (await fetch("/api/status")).json()).documents;
    const before = await count();
    const r = await post("/api/capture-page");
    if (!r.ok) return msg("Error " + r.status, "bad");
    msg("Saving the page shown in the portal window…");
    for (let i = 0; i < 16; i++) {
      await new Promise((res) => setTimeout(res, 750));
      if ((await count()) > before) { msg("Saved: it's waiting on Check.", "ok"); setTimeout(reload, 800); return; }
    }
    msg("Nothing arrived. Is a portal window open and showing the document?", "warn");
  }

  if ($("open")) $("open").addEventListener("click", openPortal);
  if ($("url")) $("url").addEventListener("keydown", (e) => { if (e.key === "Enter") openPortal(); });
  if ($("save")) $("save").addEventListener("click", savePage);

  // -- uploads
  async function upload(file, params) {
    const q = new URLSearchParams(params || {}).toString();
    const r = await fetch("/api/upload" + (q ? "?" + q : ""), {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
      body: file,
    });
    if (!r.ok) throw new Error(await errorOf(r));
    return r.json();
  }

  function describe(res) {
    const what = res.doc_type || "unclassified";
    const where = res.linked_to.length ? "matched to " + res.linked_to.join(", ") : "not matched to any checklist row";
    return (res.duplicate ? "already in the vault; " : "") + what + (res.tax_year ? " " + res.tax_year : "") + ", " + where +
      (res.note ? " (" + res.note + ")" : "");
  }

  // -- checklist rows: verify / undo (second click confirms), upload for this row
  const rowFile = $("rowfile");
  let uploadRow = null, uploadAs = null;
  document.querySelectorAll(".act-upload").forEach((b) => b.addEventListener("click", () => {
    uploadRow = b.closest("tr"); uploadAs = b.dataset.as || null;
    rowFile.click();
  }));
  if (rowFile) rowFile.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file || !uploadRow) return;
    const id = uploadRow.dataset.id;
    msg("Adding " + file.name + " to " + id + "…");
    try {
      const params = { target: id, year: uploadRow.closest("table").dataset.year };
      if (uploadAs) params.as_type = uploadAs;  // e.g. a donation's letter vs its 8283 support
      const res = await upload(file, params);
      msg(file.name + ": " + describe(res), "ok");
      setTimeout(reload, 1200);
    } catch (err) {
      msg(file.name + ": " + err.message, "bad");
    }
  });

  // A button that asks "Confirm?" on the first click and acts on the second.
  function confirmButton(b, action, ask = "Confirm?") {
    const label = b.textContent;
    let timer = null;
    b.addEventListener("click", async () => {
      if (!b.classList.contains("confirm")) {
        b.classList.add("confirm");
        b.textContent = ask;
        timer = setTimeout(() => { b.classList.remove("confirm"); b.textContent = label; }, 4000);
        return;
      }
      clearTimeout(timer);
      b.disabled = true;
      const r = await action();
      if (r.ok) reload();
      else { msg(await errorOf(r), "bad"); b.disabled = false; b.classList.remove("confirm"); b.textContent = label; }
    });
  }
  const rowId = (b) => b.closest("tr").dataset.id;

  // Documents: any year's, or all
  if ($("documents-year")) $("documents-year").addEventListener("change", (e) => {
    location.href = e.target.value === "all" ? "/documents?all=1" : `/documents?year=${e.target.value}`;
  });
  if ($("rematch")) $("rematch").addEventListener("click", async () => {
    const b = $("rematch");
    b.disabled = true;
    msg("Matching…");
    const r = await post("/api/rematch");
    if (!r.ok) { msg(await errorOf(r), "bad"); b.disabled = false; return; }
    const j = await r.json();
    const tied = j.tied ? " " + j.tied + " fit more than one row equally: attach them with ••• → Edit…." : "";
    msg(j.linked.length + " of " + j.checked + " unmatched document(s) linked." + tied, j.linked.length ? "ok" : "warn");
    if (j.linked.length) setTimeout(reload, 1500); else b.disabled = false;
  });

  // -- sharing on the network (this Mac only) and pairing (the other device)
  const sharing = (on) => async () => {
    msg(on ? "Starting…" : "Stopping…");
    const r = await post("/api/sharing", { on });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  };
  if ($("share-on")) $("share-on").addEventListener("click", sharing(true));
  if ($("share-off")) confirmButton($("share-off"), () => post("/api/sharing", { on: false }));
  if ($("pair")) $("pair").addEventListener("click", async () => {
    const r = await post("/api/pairing");
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  });
  document.querySelectorAll(".act-revoke").forEach((b) => confirmButton(b,
    () => post("/api/devices/revoke", { id: b.dataset.id })));

  if ($("pair-go")) {
    const go = async () => {
      const r = await post("/api/pair", { code: $("pair-code").value, name: $("pair-name").value });
      if (r.ok) { msg("Paired. Opening TaxBinder…", "ok"); setTimeout(() => { location.href = "/"; }, 600); }
      else msg(await errorOf(r), "bad");
    };
    $("pair-go").addEventListener("click", go);
    $("pair-name").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }

  // -- downloads: a browser downloads; the app window saves into this Mac's Downloads folder
  const remoteBrowser = document.body.dataset.remote === "true";
  async function download(ids, year) {
    if (remoteBrowser) {
      const q = year ? "year=" + year : "ids=" + encodeURIComponent(ids.join(","));
      location.href = !year && ids.length === 1 ? "/documents/" + ids[0] + "/file" : "/documents/zip?" + q;
      return;
    }
    msg("Saving…");
    const r = await post("/api/documents/save", year ? { year } : { ids });
    if (r.ok) { const j = await r.json(); msg("Saved to Downloads: " + j.name, "ok"); }
    else msg(await errorOf(r), "bad");
  }
  document.querySelectorAll(".act-download").forEach((b) => b.addEventListener("click", (e) => {
    e.preventDefault();
    download(b.dataset.ids ? b.dataset.ids.split(",") : [], b.dataset.year ? parseInt(b.dataset.year, 10) : null);
  }));
  const picks = () => Array.from(document.querySelectorAll(".dl-pick:checked")).map((c) => c.value);
  const updatePicks = () => {
    if (!$("dl-selected")) return;
    $("dl-count").textContent = picks().length;
    $("dl-selected").disabled = picks().length === 0;
    if ($("chat-prep")) $("chat-prep").disabled = picks().length === 0;
    if ($("del-selected")) {
      const del = $("del-selected"), n = picks().length;
      del.disabled = n === 0; del.classList.remove("confirm");
      del.textContent = n ? `Delete selected (${n})` : "Delete selected";
    }
  };
  document.querySelectorAll(".dl-pick").forEach((c) => c.addEventListener("change", updatePicks));
  if ($("dl-all")) $("dl-all").addEventListener("change", (e) => {
    document.querySelectorAll(".dl-pick").forEach((c) => { c.checked = e.target.checked; });
    updatePicks();
  });
  if ($("dl-selected")) $("dl-selected").addEventListener("click", () => download(picks(), null));
  // Delete several: the first click asks, the second deletes them all as one change (one Undo)
  if ($("del-selected")) $("del-selected").addEventListener("click", async () => {
    const b = $("del-selected"), ids = picks();
    if (!b.classList.contains("confirm")) {
      b.classList.add("confirm");
      b.textContent = `Delete ${ids.length} document${ids.length === 1 ? "" : "s"}?`;
      setTimeout(updatePicks, 4000);
      return;
    }
    b.disabled = true;
    const r = await post("/api/documents/delete", { ids });
    if (r.ok) reload(); else { msg(await errorOf(r), "bad"); updatePicks(); }
  });

  // -- payments
  document.querySelectorAll(".act-pay-delete").forEach((b) => confirmButton(b,
    () => post("/api/payments/delete", { id: rowId(b) })));

  // -- bills: add (with installments), schedule, mark paid, add to Calendar
  const today = () => new Date().toISOString().slice(0, 10);
  function billRow(label, due, amount) {
    const row = document.createElement("div"); row.className = "bill-row";
    const mk = (cls, ph, val, type) => { const i = document.createElement("input"); i.className = cls; i.placeholder = ph; i.value = val || ""; if (type) i.type = type; return i; };
    const del = document.createElement("button"); del.type = "button"; del.className = "link"; del.textContent = "✕"; del.setAttribute("aria-label", "Remove installment");
    del.addEventListener("click", () => row.remove());
    row.append(mk("b-label", "label, e.g. 1st installment", label), mk("b-due", "", due, "date"), mk("b-amount num", "$0.00", amount), del);
    $("bill-rows").appendChild(row);
  }
  // One dialog for every payment: kind (estimated tax, property tax, vehicle / DMV, other) and
  // whether it's already paid (date, amount, receipt) or to pay later (installments, bill).
  let apLater = false;
  function apRefresh(prefill) {
    const kind = $("ap-kind").value, tax = kind === "tax_payment";
    document.querySelectorAll("#add-pay .tax-only").forEach((f) => { f.hidden = !tax; });
    document.querySelectorAll("#add-pay .other-only").forEach((f) => { f.hidden = tax; });
    document.querySelectorAll("#add-pay .paid-only").forEach((f) => { f.hidden = apLater; });
    document.querySelectorAll("#add-pay .later-only").forEach((f) => { f.hidden = !apLater; });
    $("ap-done").classList.toggle("on", !apLater); $("ap-later").classList.toggle("on", apLater);
    $("ap-payee").placeholder = { property_tax: "e.g. County Tax Collector", vehicle_fee: "e.g. DMV" }[kind] || "e.g. HOA, insurer";
    $("ap-label").placeholder = { property_tax: "e.g. 1st installment", vehicle_fee: "e.g. registration renewal, SUV" }[kind] || "what it's for";
    $("ap-hint").hidden = !tax;
    $("ap-save").textContent = apLater ? "Plan payment(s)" : "Add payment";
    if (prefill) {
      const rows = $("bill-rows"); rows.textContent = "";
      if (kind === "property_tax") { billRow("1st installment"); billRow("2nd installment"); }  // the bill's own dates
      else billRow("", "");
    }
  }
  if ($("add-pay-open")) {
    $("add-pay-open").addEventListener("click", () => {
      apLater = false; $("ap-date").value = today(); apRefresh(true); openDialog("add-pay");
    });
    $("ap-kind").addEventListener("change", () => apRefresh(true));
    $("ap-done").addEventListener("click", () => { apLater = false; apRefresh(false); });
    $("ap-later").addEventListener("click", () => { apLater = true; apRefresh(false); });
    $("bill-add-row").addEventListener("click", () => billRow("", ""));
    $("ap-save").addEventListener("click", async () => {
      const kind = $("ap-kind").value;
      if (!apLater) {
        const r = await post("/api/payments", {
          category: kind, jurisdiction: $("ap-juris").value, kind: $("ap-qtr").value,
          payee: $("ap-payee").value, label: $("ap-label").value,
          date: $("ap-date").value, amount: $("ap-amount").value,
        });
        if (!r.ok) { msg(await errorOf(r), "bad"); return; }
        const id = (await r.json()).id, file = $("ap-file").files[0];
        if (file) {
          try { await upload(file, { target: id }); } catch (e) { msg("Added; the receipt didn't upload: " + e.message, "bad"); return; }
        }
        setTimeout(reload, 700);
        return;
      }
      let docId = null;
      const file = $("ap-billfile").files[0];
      if (file) {
        try { docId = (await upload(file, {})).doc_id; } catch (e) { msg("The bill file: " + e.message, "bad"); return; }
      }
      const installments = Array.from(document.querySelectorAll("#bill-rows .bill-row")).map((row) => ({
        label: row.querySelector(".b-label").value, due: row.querySelector(".b-due").value, amount: row.querySelector(".b-amount").value,
      }));
      const r = await post("/api/bills", {
        kind, payee: $("ap-payee").value, jurisdiction: $("ap-juris").value,
        payment_kind: $("ap-qtr").value, installments, doc_id: docId,
      });
      if (r.ok) setTimeout(reload, 700); else msg(await errorOf(r), "bad");
    });
  }
  async function addToCalendar(id) {
    if (remoteBrowser) { location.href = "/payments/calendar?id=" + encodeURIComponent(id); return true; }
    const r = await post("/api/bills/calendar", { id });
    if (r.ok) { msg("Opened in Calendar: " + (await r.json()).saved, "ok"); return true; }
    msg(await errorOf(r), "bad"); return false;
  }
  let billTarget = null;
  document.querySelectorAll(".act-schedule").forEach((b) => b.addEventListener("click", () => {
    billTarget = b.closest("tr").dataset.id;
    $("sch-date").value = b.dataset.payOn || ""; $("sch-note").value = b.dataset.note || "";
    if (remoteBrowser) $("sch-cal").checked = false;
    openDialog("schedule-dlg");
  }));
  if ($("sch-save")) $("sch-save").addEventListener("click", async () => {
    const r = await post("/api/bills/schedule", { id: billTarget, pay_on: $("sch-date").value, note: $("sch-note").value });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    if ($("sch-cal").checked) await addToCalendar(billTarget);
    setTimeout(reload, 1200);
  });
  document.querySelectorAll(".act-paid").forEach((b) => b.addEventListener("click", () => {
    billTarget = b.closest("tr").dataset.id;
    $("paid-date").value = today(); $("paid-amount").value = b.dataset.amount || ""; $("paid-file").value = "";
    openDialog("paid-dlg");
  }));
  if ($("paid-save")) $("paid-save").addEventListener("click", async () => {
    const r = await post("/api/bills/paid", { id: billTarget, paid_on: $("paid-date").value, amount: $("paid-amount").value });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    const newId = (await r.json()).id, file = $("paid-file").files[0];
    if (file) {
      try { await upload(file, { target: newId }); } catch (e) { msg("Marked paid; the confirmation didn't upload: " + e.message, "bad"); return; }
    }
    msg("Marked paid", "ok"); setTimeout(reload, 800);
  });
  document.querySelectorAll(".act-calendar").forEach((b) => b.addEventListener("click", () => addToCalendar(b.closest("tr").dataset.id)));

  // -- donations
  if ($("add-gift-open")) {
    const secFields = () => document.querySelectorAll("#add-gift .sec-only").forEach((f) => { f.hidden = $("g-kind").value !== "securities"; });
    let editing = null;  // the gift being edited, else adding one
    const giftDialog = (row) => {
      editing = row ? row.dataset.id : null;
      const d = row ? row.dataset : {};
      $("g-payee").value = d.payee || ""; $("g-date").value = d.date || today(); $("g-amount").value = d.amount || "";
      $("g-kind").value = d.kind || "cash"; $("g-security").value = d.security || ""; $("g-shares").value = d.shares || "";
      $("g-basis").value = d.basis || ""; $("g-acquired").value = d.acquired || "";
      $("g-daf").checked = !!d.daf; $("g-qpq").checked = !!d.qpq; $("g-file").value = "";
      const t = $("add-gift-title"); t.textContent = row ? t.dataset.edit : t.dataset.add;
      $("g-add").textContent = row ? "Save" : "Add donation";
      document.querySelectorAll("#add-gift .add-only").forEach((f) => { f.hidden = !!row; });
      secFields(); openDialog("add-gift");
    };
    $("add-gift-open").addEventListener("click", () => giftDialog(null));
    document.querySelectorAll(".act-gift-edit").forEach((b) => b.addEventListener("click", () => giftDialog(b.closest("tr"))));
    $("g-kind").addEventListener("change", secFields);
    $("g-add").addEventListener("click", async () => {
      const r = await post(editing ? "/api/donations/edit" : "/api/donations", {
        id: editing, payee: $("g-payee").value, date: $("g-date").value, amount: $("g-amount").value, kind: $("g-kind").value,
        security: $("g-security").value, shares: $("g-shares").value, cost_basis: $("g-basis").value,
        acquired: $("g-acquired").value, to_daf: $("g-daf").checked, quid_pro_quo: $("g-qpq").checked,
      });
      if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      const id = (await r.json()).id, file = editing ? null : $("g-file").files[0];
      if (file) {
        try { await upload(file, { target: id, as_type: "donation_ack" }); } catch (e) { msg("Added; the letter didn't upload: " + e.message, "bad"); return; }
      }
      setTimeout(reload, 700);
    });
  }
  document.querySelectorAll(".act-gift-delete").forEach((b) => confirmButton(b,
    () => post("/api/donations/delete", { id: rowId(b) })));

  // -- foreign accounts: add, edit, last year's again, delete
  if ($("add-fa-open")) {
    let editing = null, again = null;  // the account being edited; last year's, added again
    const faDialog = (d, edit) => {
      editing = edit ? d.id : null; again = edit ? null : d.source || null;
      $("fa-bank").value = d.bank || ""; $("fa-account").value = d.account || "";
      if (d.owner) $("fa-owner").value = d.owner;
      $("fa-currency").value = d.currency || ""; $("fa-max").value = edit ? d.max || "" : "";
      $("fa-interest").value = edit ? d.interest || "" : ""; $("fa-rate").value = edit ? d.rate || "" : "";
      document.querySelectorAll("#add-fa .fa-new").forEach((f) => { f.hidden = !!edit || !!again; });  // bank and account stay
      const t = $("add-fa-title"); t.textContent = edit ? t.dataset.edit : t.dataset.add;
      $("fa-save").textContent = edit ? "Save" : "Add account";
      openDialog("add-fa");
      if (d.bank && !edit) $("fa-max").focus();
    };
    $("add-fa-open").addEventListener("click", () => faDialog({}, false));
    document.querySelectorAll(".act-fa-edit").forEach((b) => b.addEventListener("click", () => faDialog(b.closest("tr").dataset, true)));
    document.querySelectorAll(".act-fa-carry").forEach((b) => b.addEventListener("click", () => faDialog(b.closest("li").dataset, false)));
    $("fa-save").addEventListener("click", async () => {
      const r = await post("/api/foreign", {
        what: editing ? "edit" : "add", id: editing, source: again, year: document.querySelector("[data-table=foreign]").dataset.year,
        bank: $("fa-bank").value, account: $("fa-account").value, owner: $("fa-owner").value,
        currency: $("fa-currency").value, max_balance: $("fa-max").value, interest: $("fa-interest").value, rate: $("fa-rate").value,
      });
      if (r.ok) setTimeout(reload, 700); else msg(await errorOf(r), "bad");
    });
  }
  document.querySelectorAll(".act-fa-delete").forEach((b) => confirmButton(b,
    () => post("/api/foreign", { what: "delete", id: rowId(b) }), "Delete?"));

  // -- prepare for chat (redacted Markdown) and the Privacy page
  if ($("chat-prep")) {
    let chatIds = [];
    $("chat-prep").addEventListener("click", async () => {
      chatIds = picks();
      msg("Redacting " + chatIds.length + " document(s)…");
      const r = await post("/api/redact", { ids: chatIds });
      if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      const j = await r.json();
      const hidden = Object.entries(j.hidden).map(([k, n]) => n + " " + k).join(", ") || "nothing";
      $("chat-summary").textContent = j.documents + " document(s) ready · hidden: " + hidden;
      const problems = [
        ...j.refused.map((x) => x.name + ": left out (" + x.why + ")"),
        ...j.scanned.map((x) => x.name + ": pages " + x.pages.join(", ") + " are scanned: they can't be checked for personal details yet, so they were left out"),
      ];
      const list = $("chat-problems"); list.textContent = ""; list.hidden = !problems.length;
      problems.forEach((t) => { const li = document.createElement("li"); li.textContent = t; list.append(li); });
      $("chat-text").value = j.markdown;
      $("chat-copy").disabled = $("chat-save").disabled = !j.documents;
      msg(""); openDialog("chat-dlg");
    });
    $("chat-copy").addEventListener("click", async () => {
      const t = $("chat-text");
      try { await navigator.clipboard.writeText(t.value); } catch (e) { t.select(); document.execCommand("copy"); }
      msg("Copied. Paste it into the chat.", "ok");
    });
    $("chat-save").addEventListener("click", async () => {
      const r = await post("/api/redact/save", { ids: chatIds });
      if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      msg("Saved to Downloads: " + (await r.json()).name, "ok");
    });
  }
  if ($("pv-add")) {
    $("pv-add").addEventListener("click", async () => {
      const r = await post("/api/privacy", { kind: $("pv-kind").value, value: $("pv-value").value, note: $("pv-note").value });
      if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      $("pv-value").value = ""; reload();
    });
    document.querySelectorAll(".act-private-remove").forEach((b) => confirmButton(b,
      () => post("/api/privacy/remove", { index: parseInt(b.closest("tr").dataset.index, 10) })));
    $("ua-go").addEventListener("click", async () => {
      const r = await post("/api/unredact", { text: $("ua-in").value });
      if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      const out = $("ua-out"); out.textContent = (await r.json()).text; out.hidden = false;
    });
  }

  // -- backup
  if ($("backup-now")) $("backup-now").addEventListener("click", async () => {
    const b = $("backup-now"); b.disabled = true; msg("Backing up…");
    const r = await post("/api/backup");
    b.disabled = false;
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    const j = await r.json();
    msg("Saved to Downloads: " + j.name + " (" + (j.size / 1e6).toFixed(1) + " MB)", "saved");
    setTimeout(reload, 1500);
  });

  // -- Report a problem: the diagnostic info, shown before it's copied
  if ($("diag-show")) $("diag-show").addEventListener("click", async () => {
    const r = await fetch("/api/diagnostics", { credentials: "same-origin" });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    $("diag-text").textContent = (await r.json()).text;
    $("diag-text").hidden = false; $("diag-copy").hidden = false;
  });
  if ($("diag-copy")) $("diag-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("diag-text").textContent); msg("Copied. Paste it into your email.", "saved"); }
    catch (e) { msg("Couldn't copy: select the text and copy it instead.", "bad"); }
  });

  // -- Files for your preparer: the .zip into Downloads; never sent
  if ($("pkg-make")) $("pkg-make").addEventListener("click", async () => {
    const b = $("pkg-make"); b.disabled = true; msg("Making the .zip…");
    const r = await post("/api/preparer/save", { year: parseInt(b.dataset.year, 10) });
    b.disabled = false;
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    const j = await r.json();
    msg("Saved to Downloads: " + j.name + " (" + j.count + " document" + (j.count === 1 ? "" : "s") + ")" +
        (j.unreadable ? "; " + j.unreadable + " couldn't be read, listed in its summary" : ""), "saved");
    setTimeout(reload, 1500);
  });

  // -- the demo: open it, start it over, leave it; its sample documents
  const demo = async (action, b) => {
    if (b) b.disabled = true;
    msg(action === "leave" ? "Back to your binder…" : "Setting up the demo…");
    const r = await post("/api/demo", { action });
    if (r.ok) location.assign((await r.json()).go || "/");
    else { msg(await errorOf(r), "bad"); if (b) b.disabled = false; }
  };
  document.querySelectorAll(".demo-start").forEach((b) => b.addEventListener("click", () => demo("start", b)));
  // -- Keep it safe: the key comes from the server only on a click, is shown, then confirmed
  const showKey = async (mac) => {
    const r = await post("/api/safe/key", { mac: !!mac });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    $("safe-key").textContent = (await r.json()).key;
    $("safe-shown").hidden = false;
    if ($("safe-hidden")) $("safe-hidden").hidden = true;
    document.body.classList.add("key-shown");  // printing shows the key sheet only
    $("safe-key").focus();  // VoiceOver reads the key first; Tab goes on to Print, Copy, confirm
  };
  if ($("safe-show")) $("safe-show").addEventListener("click", () => showKey(false));
  if ($("safe-mac")) $("safe-mac").addEventListener("click", (e) => { e.preventDefault(); showKey(true); });
  if ($("safe-copy")) $("safe-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("safe-key").textContent); msg("Copied. Paste it into your password manager.", "saved"); }
    catch (e) { msg("Couldn't copy: select the key and copy it instead.", "bad"); }
  });
  if ($("safe-confirm")) $("safe-confirm").addEventListener("click", async () => {
    const r = await post("/api/safe/confirm", { last4: $("safe-last4").value });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    $("safe-key").textContent = "";  // gone from the page as well as from the Keychain
    msg("Saved. Your recovery key is no longer kept on this Mac.", "saved");
    setTimeout(reload, 1200);
  });
  if ($("safe-later")) $("safe-later").addEventListener("click", async () => {
    const r = await post("/api/safe/later");
    if (r.ok) location.assign((await r.json()).go || "/"); else msg(await errorOf(r), "bad");
  });

  // -- documents: edit / delete
  let docEditing = null;
  document.querySelectorAll(".act-doc-edit").forEach((b) => b.addEventListener("click", () => {
    closeMenus(); const d = b.closest("tr").dataset; docEditing = d;
    $("ed-name").textContent = d.name; $("ed-type").value = d.type; $("ed-year").value = d.year; $("ed-attach").value = "";
    $("ed-issuer").value = d.issuer || ""; $("ed-account").value = "";
    const last4 = d.acct;
    $("ed-account").placeholder = last4 ? "…" + last4 : "None read";
    $("ed-account-now").textContent = last4 ? "Type the whole number to change it" : "";
    const mine = d.whose ? d.whose.split(",") : [];
    document.querySelectorAll("#ed-whose input").forEach((c) => { c.checked = mine.includes(c.value); });
    const box = $("ed-linked"); box.textContent = "";
    const ids = d.linked ? d.linked.split(",") : [];
    if (!ids.length) box.textContent = "Nothing yet.";
    ids.forEach((id) => {
      const l = document.createElement("label"), c = document.createElement("input");
      c.type = "checkbox"; c.checked = true; c.value = id; l.append(c, " " + id); box.append(l);
    });
    openDialog("edit-doc");
  }));
  if ($("ed-save")) $("ed-save").addEventListener("click", async () => {
    const d = docEditing, body = { id: d.id };
    if ($("ed-type").value !== d.type || $("ed-year").value.trim() !== d.year) {
      body.doc_type = $("ed-type").value; body.tax_year = $("ed-year").value.trim();
    }
    if ($("ed-issuer").value.trim() !== (d.issuer || "")) body.issuer = $("ed-issuer").value.trim();
    if ($("ed-account").value.trim()) body.account = $("ed-account").value.trim();
    const whose = [...document.querySelectorAll("#ed-whose input:checked")].map((c) => c.value);
    const was = d.whose ? d.whose.split(",") : [];
    if (whose.length && [...whose].sort().join() !== [...was].sort().join()) body.taxpayers = whose;
    body.detach = [...document.querySelectorAll("#ed-linked input:not(:checked)")].map((c) => c.value);
    body.attach = $("ed-attach").value.trim();
    const r = await post("/api/documents/edit", body);
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    setTimeout(reload, 500);
  });
  let whoseDoc = null;
  document.querySelectorAll(".act-whose").forEach((b) => b.addEventListener("click", () => {
    const d = b.closest("tr").dataset; whoseDoc = d.id;
    const chosen = d.whose ? d.whose.split(",") : [];
    $("wh-printed").textContent = d.printed;
    document.querySelectorAll("#wh-people input[value]").forEach((c) => { c.checked = chosen.includes(c.value); });
    $("wh-new-trust").checked = false;
    $("wh-trust-name").textContent = d.printed;
    $("wh-new-trust-row").hidden = !/\btrust\b/i.test(d.printed);
    openDialog("whose-dlg");
  }));
  if ($("wh-save")) $("wh-save").addEventListener("click", async () => {
    const taxpayers = [...document.querySelectorAll("#wh-people input[value]:checked")].map((c) => c.value);
    const r = await post("/api/documents/whose", { id: whoseDoc, taxpayers, new_trust: $("wh-new-trust").checked });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    setTimeout(reload, 500);
  });
  document.querySelectorAll(".act-move").forEach((b) => b.addEventListener("click", async () => {
    const r = await post("/api/documents/move", { id: b.closest("tr").dataset.id, to: b.dataset.to });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    setTimeout(reload, 500);
  }));
  document.querySelectorAll(".merge-type").forEach((b) => b.addEventListener("click", async () => {
    const r = await post("/api/doctypes/merge", { from: b.dataset.from, to: b.dataset.to });
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    setTimeout(reload, 500);
  }));
  document.querySelectorAll(".ret-status").forEach((s) => s.addEventListener("change", async () => {
    const r = await post("/api/returns/edit", { id: s.closest("tr").dataset.id, status: s.value });
    if (!r.ok) msg(await errorOf(r), "bad");
  }));
  // -- household: Looks right, or Change
  const houseBox = $("household");
  if (houseBox) {
    const data = JSON.parse($("house-data").textContent);
    const save = async (people, filing_status, state) => {
      const r = await post("/api/household/confirm", {
        year: Number(houseBox.dataset.year), return_year: houseBox.dataset.returnYear || null,
        people, filing_status, state });
      if (r.ok) reload(); else msg(await errorOf(r), "bad");
    };
    const row = (p) => {
      const tr = document.createElement("tr");
      tr.dataset.id = p.id || ""; tr.dataset.kind = p.kind || "individual";
      const who = p.kind === "trust" ? '<span class="muted">trust</span>'
        : `<select class="select hd-role" aria-label="Who"><option value="">Who is this?</option>${
          ["taxpayer", "spouse", "dependent"].map((r) => `<option value="${r}"${p.role === r ? " selected" : ""}>${r}</option>`).join("")}</select>`;
      tr.innerHTML = `<td><input class="hd-name" aria-label="Name"></td><td>${who}</td>
        <td>${p.kind === "trust" ? "" : '<input class="hd-last4" inputmode="numeric" maxlength="4" size="5" aria-label="Last 4 of SSN">'}</td>
        <td><button class="link hd-remove" type="button" aria-label="Remove">✕</button></td>`;
      tr.querySelector(".hd-name").value = p.name || "";
      if (tr.querySelector(".hd-last4")) tr.querySelector(".hd-last4").value = p.last4 || "";
      tr.querySelector(".hd-remove").addEventListener("click", () => tr.remove());
      $("hd-people").appendChild(tr);
    };
    document.querySelectorAll(".act-house-ok").forEach((b) => b.addEventListener("click", () =>
      save(data.people, data.filing_status, data.state)));
    document.querySelectorAll(".act-house-change").forEach((b) => b.addEventListener("click", () => {
      $("hd-people").innerHTML = "";
      (data.people.length ? data.people : [{ role: "taxpayer" }]).forEach(row);
      $("hd-status").value = data.filing_status || ""; $("hd-state").value = data.state || "";
      openDialog("house-dlg");
    }));
    $("hd-add").addEventListener("click", () => row({}));
    $("hd-add-trust").addEventListener("click", () => row({ kind: "trust" }));
    $("hd-save").addEventListener("click", () => save(
      [...$("hd-people").querySelectorAll("tr")].map((tr) => ({
        id: tr.dataset.id || null, kind: tr.dataset.kind,
        name: tr.querySelector(".hd-name").value.trim(),
        role: tr.querySelector(".hd-role") ? tr.querySelector(".hd-role").value || null : null,
        last4: tr.querySelector(".hd-last4") ? tr.querySelector(".hd-last4").value.trim() || null : null,
      })), $("hd-status").value || null, $("hd-state").value || null));
  }
  // -- settings
  document.querySelectorAll(".act-setting").forEach((s) => s.addEventListener("change", async () => {
    const r = await post("/api/settings", { key: s.dataset.key, value: s.value });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  }));
  document.querySelectorAll(".act-reveal-inbox").forEach((b) => b.addEventListener("click", () =>
    post("/api/settings/reveal-inbox", {})));
  // -- the reading model: downloads with progress, which one is in use
  const modelOf = (b) => b.closest("[data-model]").dataset.model;
  document.querySelectorAll(".act-model-download").forEach((b) => confirmButton(b,
    () => post("/api/reading/download", { model: modelOf(b) })));
  document.querySelectorAll(".act-model-delete").forEach((b) => confirmButton(b,
    () => post("/api/reading/delete", { model: modelOf(b) })));
  document.querySelectorAll(".act-model-use").forEach((b) => b.addEventListener("click", async () => {
    const r = await post("/api/reading/use", { model: modelOf(b) });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  }));
  document.querySelectorAll(".act-model-on").forEach((s) => s.addEventListener("change", async () => {
    const r = await post("/api/reading/use", { on: s.value === "on" });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  }));
  document.querySelectorAll(".act-model-cancel").forEach((b) => b.addEventListener("click", async () => {
    await post("/api/reading/cancel", {});
  }));
  document.querySelectorAll(".act-model-check").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await post("/api/reading/check", {});
    if (r.ok) reload(); else { msg(await errorOf(r), "bad"); b.disabled = false; }
  }));
  if ($("model-bar")) {
    const gb = (n) => (n / 1e9).toFixed(1) + " GB";
    const tick = async () => {
      const r = await fetch("/api/reading");
      if (!r.ok) return;
      const j = await r.json();
      if (!j.download) { reload(); return; }
      $("model-bar").value = j.download.done;
      $("model-done").textContent = gb(j.download.done) + " of " + gb(j.download.size);
      setTimeout(tick, 1000);
    };
    tick();
  }
  document.querySelectorAll(".act-year-save").forEach((b) => b.addEventListener("click", async () => {
    const tr = b.closest("tr");
    const states = [tr.querySelector(".y-state").value, tr.querySelector(".y-moved-to").value].filter(Boolean);
    const r = await post("/api/years/set", { year: Number(tr.dataset.year),
      filing_status: tr.querySelector(".y-status").value || null, states,
      moved_on: tr.querySelector(".y-moved-on").value || null });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  }));
  document.querySelectorAll(".act-add-listed").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    const r = await post("/api/returns/add-listed", { id: b.dataset.id, year: Number(b.dataset.year) });
    if (r.ok) reload(); else { msg(await errorOf(r), "bad"); b.disabled = false; }
  }));
  // -- for my preparer: a file here, handed over as it is
  let ppDoc = null;
  document.querySelectorAll(".act-pp-mark").forEach((b) => b.addEventListener("click", () => {
    const row = b.closest("tr"); ppDoc = row.dataset.id;
    $("pm-name").textContent = row.dataset.name;
    if (row.dataset.year) $("pm-year").value = row.dataset.year;
    $("pm-note").value = "";
    closeMenus(); openDialog("pp-mark");
  }));
  if ($("pm-save")) $("pm-save").addEventListener("click", async () => {
    const r = await post("/api/preparer/edit", { id: ppDoc, what: "mark", year: $("pm-year").value, note: $("pm-note").value });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  });
  document.querySelectorAll(".act-pp-unmark").forEach((b) => b.addEventListener("click", async () => {
    const r = await post("/api/preparer/edit", { id: b.closest("tr").dataset.id, what: "unmark" });
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  }));
  document.querySelectorAll(".act-doc-delete").forEach((b) => confirmButton(b,
    () => post("/api/documents/delete", { id: b.closest("tr").dataset.id })));

  // -- payments: edit
  let payEditing = null;
  document.querySelectorAll(".act-pay-edit").forEach((b) => b.addEventListener("click", () => {
    const d = b.closest("tr").dataset; payEditing = d.id;
    $("ep-date").value = d.date; $("ep-amount").value = d.amount;
    $("ep-payee").value = d.payee || ""; $("ep-label").value = d.label || "";
    document.querySelectorAll("#edit-pay .ep-plan").forEach((f) => { f.hidden = d.payee === undefined; });
    openDialog("edit-pay");
  }));
  if ($("ep-save")) $("ep-save").addEventListener("click", async () => {
    const body = { id: payEditing, date: $("ep-date").value, amount: $("ep-amount").value };
    if (!$("ep-payee").closest(".field").hidden) { body.payee = $("ep-payee").value; body.label = $("ep-label").value; }
    const r = await post("/api/payments/edit", body);
    if (!r.ok) { msg(await errorOf(r), "bad"); return; }
    setTimeout(reload, 500);
  });

  // -- Start: Get started, unlock with the recovery key, restore a backup; then the page
  // the server names (the next thing to do).
  const started = async (r) => {
    if (r.ok) { location.replace((await r.json()).go || "/"); return; }
    msg(await errorOf(r), "bad");
    document.querySelectorAll("#get-started, #unlock-go, #restore-go").forEach((b) => { b.disabled = false; });
  };
  if ($("get-started")) $("get-started").addEventListener("click", async () => {
    $("get-started").disabled = true;
    started(await post("/api/start"));
  });
  if ($("unlock-go")) $("unlock-go").addEventListener("click", async () => {
    if (!$("unlock-key").value.trim()) { msg("Type your recovery key first.", "bad"); return; }
    $("unlock-go").disabled = true;
    started(await post("/api/start/unlock", { key: $("unlock-key").value }));
  });
  if ($("retry-go")) $("retry-go").addEventListener("click", async () => {
    $("retry-go").disabled = true;
    const r = await post("/api/start/retry");
    if (r.ok) { location.assign((await r.json()).go || "/"); return; }
    msg(await errorOf(r), "bad"); $("retry-go").disabled = false;
  });
  if ($("restore-go")) $("restore-go").addEventListener("click", async () => {
    const file = $("restore-file").files[0], key = $("restore-key").value.trim();
    if (!file || !key) { msg("Choose the backup file and type your recovery key.", "bad"); return; }
    $("restore-go").disabled = true;
    msg("Restoring… this can take a minute for a large binder.");
    started(await fetch("/api/start/restore", {
      method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Recovery-Key": key }, body: file,
    }));
  });

  // -- Check: each answer is one undoable change; the page reloads to show what's left
  const docOf = (el) => el.closest("[data-id]");
  const answer = async (path, body) => {
    const r = await post(path, body);
    if (r.ok) reload(); else msg(await errorOf(r), "bad");
  };
  document.querySelectorAll(".chk-mark").forEach((b) => b.addEventListener("click", () =>
    answer("/api/check/mark", { id: docOf(b).dataset.id, what: b.dataset.what })));
  document.querySelectorAll(".chk-whose").forEach((b) => b.addEventListener("click", () =>
    answer("/api/documents/whose", { id: docOf(b).dataset.id, taxpayers: b.dataset.whose.split(",") })));
  // Delete: the first click asks, the second deletes (one change; Undo puts it back)
  document.querySelectorAll(".chk-del").forEach((b) => confirmButton(b,
    () => post("/api/documents/delete", { id: docOf(b).dataset.id }), "Delete?"));
  document.querySelectorAll(".chk-drop").forEach((b) => b.addEventListener("click", () =>
    answer("/api/documents/delete", { id: docOf(b).dataset.id })));
  document.querySelectorAll(".chk-return").forEach((b) => b.addEventListener("click", () =>
    answer("/api/returns/edit", { id: docOf(b).dataset.id, status: b.dataset.status })));
  const picked = (b, cls) => docOf(b).querySelector(cls).value.trim();
  document.querySelectorAll(".chk-type").forEach((b) => b.addEventListener("click", () => {
    const v = picked(b, ".chk-type-pick");
    if (v) answer("/api/check/edit", { id: docOf(b).dataset.id, doc_type: v }); else msg("Choose what it is first.", "bad");
  }));
  document.querySelectorAll(".chk-year").forEach((b) => b.addEventListener("click", () =>
    answer("/api/check/edit", { id: docOf(b).dataset.id, tax_year: picked(b, ".chk-year-pick") })));
  document.querySelectorAll(".chk-issuer").forEach((b) => b.addEventListener("click", () =>
    answer("/api/check/edit", { id: docOf(b).dataset.id, issuer: picked(b, ".chk-issuer-pick") })));
  document.querySelectorAll(".chk-use-model").forEach((b) => b.addEventListener("click", () => {
    const body = { id: docOf(b).dataset.id };  // what the model read, only where it differs
    if (b.dataset.issuer) body.issuer = b.dataset.issuer;
    if (b.dataset.year) body.tax_year = b.dataset.year;
    answer("/api/check/edit", body);
  }));
  // a key box in doubt: the rules' amount, the model's, or typed
  const boxAnswer = (b, value) => answer("/api/check/box", { id: docOf(b).dataset.id, box: b.closest(".chk-box").dataset.box, value });
  document.querySelectorAll(".chk-box-use").forEach((b) => b.addEventListener("click", () => boxAnswer(b, b.dataset.value)));
  document.querySelectorAll(".chk-box-save").forEach((b) => b.addEventListener("click", () => {
    const v = b.closest(".chk-box").querySelector(".chk-box-pick").value.trim();
    if (v) boxAnswer(b, v); else msg("Type the amount first.", "bad");
  }));
  document.querySelectorAll(".chk-merge").forEach((b) => b.addEventListener("click", () => {
    const row = b.closest(".merge-row"), into = row.querySelector(".chk-merge-pick").value;
    if (into) answer("/api/check/merge", { from: row.dataset.from, into }); else msg("Choose the place it's the same as.", "bad");
  }));
  let editing = null;
  document.querySelectorAll(".chk-edit").forEach((b) => b.addEventListener("click", () => {
    editing = docOf(b).dataset;
    $("dd-name").textContent = editing.name + (editing.account ? " · account " + editing.account : "");
    $("dd-type").value = editing.type; $("dd-year").value = editing.year; $("dd-issuer").value = editing.issuer;
    $("dd-account").value = ""; $("dd-account").placeholder = editing.account || "not read";
    const whose = editing.whose ? editing.whose.split(",") : [];
    document.querySelectorAll(".dd-whose").forEach((c) => { c.checked = whose.includes(c.value); });
    if ($("dd-filer")) $("dd-filer").value = "";
    openDialog("doc-dlg");
  }));
  if ($("dd-save")) $("dd-save").addEventListener("click", async () => {
    const id = editing.id, body = { id };
    if (editing.later) body.back = true;  // changed on a Not filed row: back with the others
    if ($("dd-type").value !== editing.type) body.doc_type = $("dd-type").value;
    if ($("dd-year").value.trim() !== editing.year) body.tax_year = $("dd-year").value.trim();
    if ($("dd-issuer").value.trim() !== editing.issuer) body.issuer = $("dd-issuer").value.trim();
    if ($("dd-account").value.trim()) body.account = $("dd-account").value.trim();
    $("dd-save").disabled = true;
    try {
      if (Object.keys(body).length > 1) {  // an answer, or `back`
        const r = await post("/api/check/edit", body);
        if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      }
      const whose = [...document.querySelectorAll(".dd-whose")].filter((c) => c.checked).map((c) => c.value);
      if (whose.join(",") !== (editing.whose || "") && whose.length) {
        const r = await post("/api/documents/whose", { id, taxpayers: whose });
        if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      }
      if ($("dd-filer") && $("dd-filer").value) {
        const r = await post("/api/documents/move", { id, to: $("dd-filer").value });
        if (!r.ok) { msg(await errorOf(r), "bad"); return; }
      }
      reload();
    } finally { $("dd-save").disabled = false; }
  });
  if ($("chk-confirm")) $("chk-confirm").addEventListener("click", async () => {
    $("chk-confirm").disabled = true;
    const r = await post("/api/check/confirm");
    if (r.ok) { const j = await r.json(); setTimeout(() => location.assign(j.go || "/"), 1200); }
    else { msg(await errorOf(r), "bad"); $("chk-confirm").disabled = false; }
  });

  // Results' "Yes, add it" lands here: open the add dialog for that kind
  const adding = new URLSearchParams(location.search).get("add");
  if (adding && $("add-pay-open")) {
    $("add-pay-open").click();
    if ([...$("ap-kind").options].some((o) => o.value === adding)) { $("ap-kind").value = adding; $("ap-kind").dispatchEvent(new Event("change")); }
  } else if (adding && $("add-gift-open")) $("add-gift-open").click();
  else if (adding && $("add-fa-open")) $("add-fa-open").click();

  // -- Print: the app window asks the shell for the Mac's print panel; a browser prints itself
  document.querySelectorAll(".act-print").forEach((b) => b.addEventListener("click", async () => {
    if (document.body.dataset.app !== "true") { window.print(); return; }
    const r = await post("/api/print");
    if (!r.ok) msg(await errorOf(r), "bad");
  }));
})();
