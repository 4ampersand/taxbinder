// Data tables (table.grid[data-table]): reorder, show/hide, resize and sort columns; group rows;
// row height; filter chips and search. The layout is remembered per table in localStorage, a
// per-device convenience only, so every read and write tolerates failure.
//
// Markup: <th data-col="key" data-w="140" [data-flex] [data-hidden] [data-locked]
// [data-sort-type="num|date"]>. Rows: <tr data-status data-text data-g-<group>>; a cell's
// data-sort overrides its text for sorting. Controls refer to the table by name:
// [data-grid-group=name] (select), [data-grid-columns=name] (button), [data-chips=name]
// (buttons with data-filter), #<name>-q.
(function () {
  const load = (key) => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ } };
  const forget = (key) => { try { localStorage.removeItem(key); } catch (e) { /* ignore */ } };
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const groupOf = (r, g) => r.dataset["g" + g[0].toUpperCase() + g.slice(1)] || "—";

  function setup(table) {
    const name = table.dataset.table;
    const key = "tb.grid." + name;
    const headRow = table.tHead.rows[0];
    const body = table.tBodies[0];
    const heads = {};
    $$("th", headRow).forEach((th) => { heads[th.dataset.col] = th; });
    const cols = Object.keys(heads);
    const isLocked = (c) => heads[c].hasAttribute("data-locked");
    const dataRows = () => $$("tr", body).filter((r) => !r.classList.contains("group") && r.cells.length > 1);
    const footRows = () => (table.tFoot ? Array.from(table.tFoot.rows) : []);  // cells carry data-c
    dataRows().forEach((r) => Array.from(r.cells).forEach((td, i) => { td.dataset.c = cols[i]; }));

    const groupSelect = document.querySelector(`[data-grid-group="${name}"]`);
    const defaults = {
      order: cols.slice(),
      hidden: cols.filter((c) => heads[c].hasAttribute("data-hidden")),
      widths: {},
      sort: null,
      group: groupSelect ? groupSelect.options[0].value : "",
    };
    let st = Object.assign({}, defaults, load(key) || {});
    st.order = st.order.filter((c) => heads[c]).concat(cols.filter((c) => !st.order.includes(c)));
    const persist = () => save(key, st);
    let filterStatuses = null, query = "";

    const isHidden = (c) => st.hidden.includes(c) || (c === "inst" && st.group === "inst");
    // Locked columns keep their place: those before the first movable one stay first (a select
    // box), the others stay last (row actions).
    const firstMovable = cols.findIndex((c) => !isLocked(c));
    const lead = cols.filter((c, i) => isLocked(c) && i < firstMovable);
    const trail = cols.filter((c, i) => isLocked(c) && i > firstMovable);
    const ordered = () => lead.concat(st.order.filter((c) => !isLocked(c)), trail);
    const visible = () => ordered().filter((c) => !isHidden(c));
    const width = (c) => st.widths[c] || parseInt(heads[c].dataset.w, 10) || 120;

    // -- columns: order, visibility, widths
    function layout() {
      const order = ordered();
      order.forEach((c) => { headRow.appendChild(heads[c]); heads[c].classList.toggle("col-hidden", isHidden(c)); });
      dataRows().concat(footRows()).forEach((r) => {
        const byCol = {};
        Array.from(r.cells).forEach((td) => { byCol[td.dataset.c] = td; });
        order.forEach((c) => { if (byCol[c]) { r.appendChild(byCol[c]); byCol[c].classList.toggle("col-hidden", isHidden(c)); } });
      });
      const shown = visible();
      const flex = shown.find((c) => heads[c].hasAttribute("data-flex"));
      let total = shown.reduce((a, c) => a + width(c), 0);
      const spare = table.parentElement.clientWidth - total - 2;
      shown.forEach((c) => { heads[c].style.width = (width(c) + (c === flex && spare > 0 ? spare : 0)) + "px"; });
      table.style.width = (total + (flex && spare > 0 ? spare : 0)) + "px";
      $$("tr.group td", body).forEach((td) => { td.colSpan = shown.length; });
    }

    // -- rows: sort, group, filter
    function value(r, c) {
      const td = r.querySelector(`td[data-c="${c}"]`);
      if (!td) return "";
      return td.dataset.sort !== undefined ? td.dataset.sort : td.textContent.trim();
    }
    function arrange() {
      $$("tr.group", body).forEach((g) => g.remove());
      let rows = dataRows();
      if (st.sort && heads[st.sort.col]) {
        const type = heads[st.sort.col].dataset.sortType || "text";
        rows.sort((a, b) => {
          const x = value(a, st.sort.col), y = value(b, st.sort.col);
          if (x === "" || y === "") return (x === "") - (y === "");  // blanks last, either way
          const d = type === "num" ? parseFloat(x) - parseFloat(y) : collator.compare(x, y);
          return st.sort.dir === "desc" ? -d : d;
        });
      }
      if (st.group) {
        const order = [];
        rows.forEach((r) => { const g = groupOf(r, st.group); if (!order.includes(g)) order.push(g); });
        if (st.group !== "kind") order.sort((a, b) => collator.compare(a, b));  // kinds keep their own order
        rows = order.flatMap((g) => rows.filter((r) => groupOf(r, st.group) === g));
        let last = null;
        rows.forEach((r) => {
          const g = groupOf(r, st.group);
          if (g !== last) {
            last = g;
            const tr = document.createElement("tr"); tr.className = "group";
            const td = document.createElement("td"); td.colSpan = visible().length; td.textContent = g;
            tr.appendChild(td); body.appendChild(tr);
          }
          body.appendChild(r);
        });
      } else rows.forEach((r) => body.appendChild(r));
      filter();
      $$("th", headRow).forEach((th) => {
        let arrow = th.querySelector(".arrow");
        if (!st.sort || st.sort.col !== th.dataset.col) { if (arrow) arrow.remove(); return; }
        if (!arrow) { arrow = document.createElement("span"); arrow.className = "arrow"; th.insertBefore(arrow, th.querySelector(".grip")); }
        arrow.textContent = st.sort.dir === "asc" ? "▲" : "▼";
      });
    }
    function filter() {
      dataRows().forEach((r) => {
        const okStatus = !filterStatuses || filterStatuses.includes(r.dataset.status);
        const okText = !query || (r.dataset.text || r.textContent.toLowerCase()).includes(query);
        r.classList.toggle("hidden", !(okStatus && okText));
      });
      totals();
      $$("tr.group", body).forEach((g) => {
        let n = g.nextElementSibling, any = false;
        while (n && !n.classList.contains("group")) { if (!n.classList.contains("hidden")) any = true; n = n.nextElementSibling; }
        g.classList.toggle("hidden", !any);
      });
    }

    // Totals row: sums of the rows shown (after filters and search).
    function totals() {
      footRows().forEach((fr) => $$("td[data-total]", fr).forEach((td) => {
        const c = td.dataset.total;
        let sum = 0, any = false;
        dataRows().filter((r) => !r.classList.contains("hidden")).forEach((r) => {
          const v = parseFloat(value(r, c));
          if (!isNaN(v)) { sum += v; any = true; }
        });
        td.textContent = any ? sum.toLocaleString("en-US", { style: "currency", currency: "USD" }) : "—";
      }));
    }
    table.addEventListener("grid:changed", totals);  // e.g. after an inline edit

    // -- header interactions: sort, drag to reorder, resize
    cols.forEach((c) => {
      const th = heads[c];
      if (isLocked(c)) return;
      const grip = document.createElement("span"); grip.className = "grip"; th.appendChild(grip);
      th.draggable = true;
      th.addEventListener("click", (e) => {
        if (e.target === grip) return;
        st.sort = { col: c, dir: st.sort && st.sort.col === c && st.sort.dir === "asc" ? "desc" : "asc" };
        persist(); arrange();
      });
      th.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", c); e.dataTransfer.effectAllowed = "move"; });
      th.addEventListener("dragover", (e) => { e.preventDefault(); th.classList.add("drop-before"); });
      th.addEventListener("dragleave", () => th.classList.remove("drop-before"));
      th.addEventListener("drop", (e) => { e.preventDefault(); th.classList.remove("drop-before"); move(e.dataTransfer.getData("text/plain"), c); });
      grip.addEventListener("click", (e) => e.stopPropagation());
      grip.addEventListener("mousedown", (e) => {
        e.preventDefault(); e.stopPropagation(); th.draggable = false;
        const startX = e.clientX, start = th.getBoundingClientRect().width;
        grip.classList.add("on");
        const drag = (ev) => { st.widths[c] = Math.max(50, Math.round(start + ev.clientX - startX)); layout(); };
        const up = () => {
          grip.classList.remove("on"); th.draggable = true;
          document.removeEventListener("mousemove", drag); document.removeEventListener("mouseup", up);
          persist();
        };
        document.addEventListener("mousemove", drag); document.addEventListener("mouseup", up);
      });
    });
    function move(from, before) {
      if (!heads[from] || from === before || isLocked(from) || isLocked(before)) return;
      st.order = st.order.filter((c) => c !== from);
      st.order.splice(st.order.indexOf(before), 0, from);
      persist(); layout(); renderMenu();
    }

    // -- the Columns menu
    const colButton = document.querySelector(`[data-grid-columns="${name}"]`);
    let menu = null;
    function renderMenu() {
      if (!menu) return;
      menu.textContent = "";
      const h = document.createElement("h4"); h.textContent = "Show and order columns"; menu.appendChild(h);
      st.order.filter((c) => !isLocked(c)).forEach((c) => {
        const item = document.createElement("label"); item.className = "col-item"; item.draggable = true; item.dataset.col = c;
        const handle = document.createElement("span"); handle.className = "handle"; handle.textContent = "⋮⋮";
        const box = document.createElement("input"); box.type = "checkbox"; box.checked = !st.hidden.includes(c);
        const text = document.createElement("span"); text.textContent = heads[c].textContent.replace(/[▲▼]/g, "").trim();
        box.setAttribute("aria-label", "Show " + text.textContent);
        item.append(handle, box, text);
        box.addEventListener("change", () => {
          st.hidden = box.checked ? st.hidden.filter((x) => x !== c) : st.hidden.concat(c);
          if (!visible().some((x) => !isLocked(x))) { box.checked = true; st.hidden = st.hidden.filter((x) => x !== c); }
          persist(); layout();
        });
        item.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", c); item.classList.add("dragging"); });
        item.addEventListener("dragend", () => item.classList.remove("dragging"));
        item.addEventListener("dragover", (e) => e.preventDefault());
        item.addEventListener("drop", (e) => { e.preventDefault(); move(e.dataTransfer.getData("text/plain"), c); });
        menu.appendChild(item);
      });
      const reset = document.createElement("button"); reset.type = "button"; reset.className = "reset"; reset.textContent = "Reset to default";
      reset.addEventListener("click", () => {
        forget(key);
        st = Object.assign({}, defaults, { order: cols.slice(), hidden: defaults.hidden.slice(), widths: {} });
        syncControls(); layout(); arrange(); renderMenu();
      });
      menu.appendChild(reset);
    }
    if (colButton) {
      colButton.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!menu) {
          menu = document.createElement("div"); menu.className = "menu"; menu.hidden = true;
          colButton.parentElement.appendChild(menu); menu.addEventListener("click", (ev) => ev.stopPropagation());
        }
        renderMenu(); menu.hidden = !menu.hidden; colButton.setAttribute("aria-expanded", String(!menu.hidden));
      });
      document.addEventListener("click", () => { if (menu) { menu.hidden = true; colButton.setAttribute("aria-expanded", "false"); } });
    }

    // -- grouping, chips, search
    function syncControls() {
      if (groupSelect) groupSelect.value = st.group;
    }
    if (groupSelect) groupSelect.addEventListener("change", () => { st.group = groupSelect.value; persist(); layout(); arrange(); });
    const chips = $$(`[data-chips="${name}"] [data-filter]`);
    chips.forEach((chip) => chip.addEventListener("click", () => {
      chips.forEach((x) => x.classList.toggle("on", x === chip));
      filterStatuses = chip.dataset.filter ? chip.dataset.filter.split(" ") : null; filter();
    }));
    const search = document.getElementById(name + "-q");
    if (search) search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); filter(); });

    syncControls(); layout(); arrange();
    window.addEventListener("resize", layout);
  }

  $$("table.grid[data-table]").forEach(setup);
})();
