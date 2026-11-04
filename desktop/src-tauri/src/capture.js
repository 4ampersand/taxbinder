// Injected into a portal window by "Save Page to TaxBinder".
// Finds the document on screen, re-fetches it with the page's own session cookies,
// and triggers a blob download. The shell's download handler captures blob downloads
// on every platform (proven on Northvale), so this reuses that path.
(async () => {
  // Progress is published on window.__taxbinderCapture; the shell polls it and logs it.
  const status = (state, detail) => { window.__taxbinderCapture = { state, detail }; };
  const say = (msg) => { console.log("[TaxBinder] " + msg); status("TP_RUNNING", msg); };
  status("TP_RUNNING", "started");

  // 1. Which URL holds the document?
  //    - the page itself when the browser's PDF viewer replaced the portal page
  //    - otherwise an embedded PDF (<embed>, <object>, <iframe>) inside a portal page
  const candidates = [];
  const ct = (document.contentType || "").toLowerCase();
  if (ct === "application/pdf" || /\.pdf($|[?#])/i.test(location.href)) {
    candidates.push(location.href);
  }
  for (const el of document.querySelectorAll(
    'embed[type="application/pdf"], object[type="application/pdf"], embed[src], object[data], iframe[src]'
  )) {
    const src = el.getAttribute("src") || el.getAttribute("data");
    if (!src) continue;
    const abs = new URL(src, location.href).href;
    const looksPdf =
      /pdf/i.test(el.getAttribute("type") || "") || /\.pdf($|[?#])/i.test(abs) || /document|statement|download|file/i.test(abs);
    if (looksPdf) candidates.push(abs);
  }
  if (!candidates.length) candidates.push(location.href); // last resort: try the page

  // 2. Fetch the first candidate that returns something other than HTML.
  for (const url of [...new Set(candidates)]) {
    try {
      const res = url.startsWith("blob:") ? await fetch(url) : await fetch(url, { credentials: "include" });
      if (!res.ok) { say("HTTP " + res.status + " for " + url); continue; }
      const type = (res.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
      if (type === "text/html") { say("skipping HTML at " + url); continue; }
      const blob = await res.blob();

      // 3. Choose a filename: Content-Disposition, then the URL, then the page title.
      let name = "";
      const cd = res.headers.get("content-disposition") || "";
      const m = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)|filename\s*=\s*"?([^";]+)"?/i.exec(cd);
      if (m) { try { name = decodeURIComponent((m[1] || m[2]).trim()); } catch (_) { name = (m[1] || m[2]).trim(); } }
      if (!name && !url.startsWith("blob:")) {
        try { name = decodeURIComponent(new URL(url).pathname.split("/").pop() || ""); } catch (_) {}
      }
      if (!name || !/\.[a-z0-9]{2,5}$/i.test(name)) {
        const base = (name || document.title || "document").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 120);
        name = base + (type === "application/pdf" || blob.type === "application/pdf" ? ".pdf" : "");
      }

      // 4. Hand it over as a download.
      const obj = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = obj;
      a.download = name;
      a.style.display = "none";
      (document.body || document.documentElement).appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(obj), 60000);
      console.log("[TaxBinder] saved " + name);
      status("TP_SAVED", name + " (" + blob.size + " bytes) from " + url.slice(0, 120));
      return;
    } catch (e) {
      say("failed for " + url + ": " + e);
    }
  }
  status("TP_FAILED", "no downloadable document found; candidates=" + JSON.stringify([...new Set(candidates)]).slice(0, 400));
})().catch((e) => { window.__taxbinderCapture = { state: "TP_FAILED", detail: String(e) }; });
