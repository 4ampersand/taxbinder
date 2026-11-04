/* The top bar on every page: which filer, adding and renaming one, and the demo's Start over
   and Leave. */
(() => {
  const say = document.getElementById("bar-say");
  const post = (url, body) => fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const failed = async (r) => {
    const text = (await r.json().catch(() => ({}))).error || "That didn't work.";
    if (say) say.textContent = text;
  };

  // which filer this window is on (its own choice)
  document.querySelectorAll("[data-entity]").forEach((b) => b.addEventListener("click", async () => {
    if (b.classList.contains("on")) return;
    const r = await post("/api/entity", { slug: b.dataset.entity });
    if (r.ok) location.reload(); else failed(r);
  }));

  // renaming one: the name becomes a box; Enter keeps it, Escape puts it back
  document.querySelectorAll("[data-rename]").forEach((b) => b.addEventListener("click", () => {
    const tab = b.previousElementSibling;
    const input = document.createElement("input");
    input.value = b.dataset.name; input.className = "rename-box";
    input.setAttribute("aria-label", "New name for " + b.dataset.name);
    tab.hidden = true; b.hidden = true; b.after(input); input.focus(); input.select();
    let done = false;
    const finish = async (keep) => {
      if (done) return; done = true;
      if (keep && input.value.trim() && input.value.trim() !== b.dataset.name) {
        const r = await post("/api/entity/rename", { slug: b.dataset.rename, name: input.value });
        if (r.ok) { location.reload(); return; }
        failed(r);
      }
      input.remove(); tab.hidden = false; b.hidden = false; b.focus();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") { e.stopPropagation(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }));

  // adding one (on this Mac): its own vault, locked with the same keys
  const dlg = document.getElementById("add-entity");
  if (dlg) {
    const open = document.getElementById("add-entity-open");
    const close = () => { dlg.hidden = true; open.focus(); };
    open.addEventListener("click", () => { dlg.hidden = false; document.getElementById("ent-name").focus(); });
    dlg.querySelectorAll('[data-close="add-entity"]').forEach((b) => b.addEventListener("click", close));
    dlg.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    document.getElementById("ent-add").addEventListener("click", async () => {
      const r = await post("/api/entities", {
        name: document.getElementById("ent-name").value, form: document.getElementById("ent-form").value });
      if (r.ok) location.reload(); else failed(r);
    });
  }

  // the demo: start it over, or leave it
  document.querySelectorAll(".demo-act").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    if (say) say.textContent = b.dataset.action === "leave" ? "Back to your binder…" : "Setting up the demo…";
    const r = await post("/api/demo", { action: b.dataset.action });
    if (r.ok) location.assign((await r.json()).go || "/");
    else { failed(r); b.disabled = false; }
  }));
})();
