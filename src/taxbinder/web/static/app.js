/* Every page: the document viewer; home's deck of years, Across the years. The page's
   policy blocks style attributes, not styles set from here: each card's place in the deck is a
   CSS variable (--p: 0 in front, 1 just behind, …) that app.css turns into its pose. */
(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // -- the viewer: pictures of a document's pages, drawn on this Mac (never the file) -----------
  const scrim = document.getElementById('viewer');
  const pages = document.getElementById('vw-pages');
  let opener = null;
  async function view(id, page = 1) {
    opener = document.activeElement;
    const r = await fetch(`/documents/${encodeURIComponent(id)}/view`);
    if (!r.ok) return;
    const info = await r.json();
    document.getElementById('vw-title').textContent = info.name || 'Document';
    pages.replaceChildren(...info.pages.map(([w, h], i) => {
      const img = new Image(w, h);
      img.alt = `Page ${i + 1} of ${info.pages.length}`;
      img.src = `/documents/${encodeURIComponent(id)}/page/${i + 1}`;
      return img;
    }));
    if (!info.pages.length) pages.textContent = 'This file can’t be shown here.';
    scrim.hidden = false;
    document.getElementById('vw-close').focus();
    const at = pages.children[page - 1];
    if (at && page > 1) at.scrollIntoView();
  }
  function closeView() { scrim.hidden = true; if (opener) opener.focus(); }
  document.getElementById('vw-close').addEventListener('click', closeView);
  // beside the page, not over it: the page stays in sight and usable
  document.getElementById('vw-wide').addEventListener('click', (e) => {
    const wide = scrim.classList.toggle('wide');
    e.currentTarget.setAttribute('aria-pressed', String(wide));
    e.currentTarget.textContent = wide ? 'Narrower' : 'Wider';
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !scrim.hidden) closeView(); });

  // anything with data-view="<document id>" (and data-page) opens it
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]');
    if (b) { e.stopPropagation(); view(b.dataset.view, Number(b.dataset.page) || 1); }
  });

  // -- Across the years: search, grouping, ✓ and ✕ ------------------------------------------------
  const table = document.getElementById('ay');
  if (table) {
    const body = table.tBodies[0];
    const rows = [...body.querySelectorAll('tr.r')];
    // a row's sub-rows (its other boxes) stay under it, open or closed with it
    const subsOf = (r) => [...body.querySelectorAll('tr.sub')].filter((s) => s.dataset.of === `${r.dataset.source}|${r.dataset.docType}`);
    const subs = new Map(rows.map((r) => [r, subsOf(r)]));
    const opened = (r) => r.querySelector('.open-sub')?.getAttribute('aria-expanded') === 'true';
    const open = (r, yes) => {
      const b = r.querySelector('.open-sub');
      if (!b) return;
      b.setAttribute('aria-expanded', String(yes));
      subs.get(r).forEach((s) => { s.hidden = r.hidden || !yes; });
    };
    const span = table.tHead.rows[0].cells.length;
    let by = 'sender';
    const show = () => {
      body.querySelectorAll('tr.group').forEach((g) => g.remove());
      const q = document.getElementById('ay-q').value.trim().toLowerCase();
      const order = [];
      rows.forEach((r) => {
        r.hidden = Boolean(q) && !r.dataset.text.includes(q);
        const g = by ? r.dataset[by] : '';
        if (!order.includes(g)) order.push(g);
      });
      if (by === 'sender') order.sort((a, b) => a.localeCompare(b));
      order.forEach((g) => {
        const these = rows.filter((r) => (by ? r.dataset[by] : '') === g);
        // a sender with one row needs no heading; under a heading, its rows don't repeat it
        const head = by === 'kind' || (by === 'sender' && these.length > 1);
        these.forEach((r) => r.classList.toggle('under', head && by === 'sender'));
        if (head && these.some((r) => !r.hidden)) {
          const tr = body.insertRow(); tr.className = 'group';
          const th = document.createElement('th'); th.colSpan = span; th.scope = 'rowgroup'; th.textContent = g;
          tr.append(th);
        }
        these.forEach((r) => { body.append(r, ...subs.get(r)); open(r, opened(r)); });
      });
    };
    const all = document.getElementById('ay-open-all');
    all?.addEventListener('click', () => {
      const yes = all.getAttribute('aria-pressed') !== 'true';
      all.setAttribute('aria-pressed', String(yes)); all.textContent = yes ? 'Close all' : 'Open all';
      rows.forEach((r) => open(r, yes));
    });
    document.getElementById('ay-q').addEventListener('input', show);
    document.querySelectorAll('[data-group]').forEach((b) => b.addEventListener('click', () => {
      by = b.dataset.group;
      document.querySelectorAll('[data-group]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      show();
    }));
    // every change goes through the app's API (undoable); one that changes more than its cell
    // reloads the page
    const say = document.getElementById('ay-say');
    const post = (what, body) => fetch(`/api/matrix/${what}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const failed = async (r) => { say.textContent = (await r.json().catch(() => ({}))).error || 'That didn’t work.'; };
    const rowOf = (el) => { const tr = el.closest('tr'); return { source: tr.dataset.source, doc_type: tr.dataset.docType }; };
    const boxOf = (el) => { const b = el.closest('tr').dataset.box; return b === undefined ? {} : { box: Number(b) }; };
    const changed = async (r) => { if (r.ok) location.reload(); else await failed(r); };

    // an amount: click or Enter on a year's cell, then type; Enter saves, Escape puts it back
    function edit(td) {
      if (td.classList.contains('editing')) return;
      const kept = [...td.childNodes], before = td.dataset.value || '';
      const input = document.createElement('input');
      input.value = before; input.inputMode = 'decimal';
      input.setAttribute('aria-label', `The ${td.dataset.year} amount`);
      td.classList.add('editing'); td.replaceChildren(input); input.focus(); input.select();
      let done = false;
      const finish = async (keep) => {
        if (done) return; done = true;
        td.classList.remove('editing');
        if (!keep || input.value.trim() === before) { td.replaceChildren(...kept); td.focus(); return; }
        input.readOnly = true; td.append(input); say.textContent = 'Saving…';  // the year is checked again
        const one = boxOf(td);  // a row with several boxes: this cell's box alone
        const r = await post('set', { ...rowOf(td), ...one, year: Number(td.dataset.year), value: input.value.trim() });
        if (!r.ok) { td.replaceChildren(...kept); await failed(r); return; }
        const j = await r.json();
        const val = document.createElement('span'); val.className = 'val';
        (one.box === undefined ? j.parts || [] : j.part ? [j.part] : []).forEach(([text, neg], i) => {
          if (i) val.append(' / ');
          const part = document.createElement('span'); part.textContent = text;
          if (neg) part.className = 'neg';
          val.append(part);
        });
        // the cell keeps its View; the ✓ ✕ and notes were about what was there before
        const view = kept.filter((n) => n.nodeType === 1)
          .flatMap((n) => (n.matches('[data-view]') ? [n] : [...n.querySelectorAll('[data-view]')]))[0];
        td.classList.remove('conflict');
        td.replaceChildren(...(view ? [view] : []), val);
        if (view) view.className = 'link peek';
        td.dataset.value = j.raw; td.title = j.tip;
        say.textContent = `Saved. Every change can be undone in Changes.`;
        td.focus();
      };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
      });
      input.addEventListener('blur', () => finish(true));
    }

    table.addEventListener('click', async (e) => {
      const b = e.target.closest('.act');
      if (b) {
        const td = b.closest('td');
        const r = await post(b.dataset.act, { ...rowOf(b), year: Number(td.dataset.year) });
        if (r.ok) location.reload(); else await failed(r);
        return;
      }
      const rm = e.target.closest('.rm-row');
      if (rm) {  // asks once more before it goes
        if (!rm.classList.contains('confirm')) {
          rm.classList.add('confirm'); rm.textContent = 'Remove it?';
          setTimeout(() => { rm.classList.remove('confirm'); rm.textContent = 'Remove'; }, 4000);
          return;
        }
        say.textContent = 'Removing…';
        changed(await post('remove', rowOf(rm)));
        return;
      }
      const os = e.target.closest('.open-sub');
      if (os) { open(os.closest('tr'), !opened(os.closest('tr'))); return; }
      const bt = e.target.closest('.bx-type');
      if (bt) { typeBoxes(bt.closest('td'), bt.dataset.was); return; }
      const td = e.target.closest('td.ed');
      if (td && !e.target.closest('button, input')) edit(td);
    });
    table.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.matches?.('td.ed')) { e.preventDefault(); edit(e.target); }
    });

    // the box a row reads: from the form's list, or typed ("Another box…")
    function typeBoxes(td, was) {
      const input = document.createElement('input');
      input.value = was; input.size = 10; input.placeholder = 'e.g. 1, 5';
      input.setAttribute('aria-label', 'Box ids, then Enter');
      const kept = [...td.childNodes];
      td.replaceChildren(input); input.focus(); input.select();
      input.addEventListener('keydown', async (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); td.replaceChildren(...kept); }
        if (e.key !== 'Enter') return;
        say.textContent = 'Reading that box from every year…';
        changed(await post('boxes', { ...rowOf(td), boxes: input.value.trim() }));
      });
    }
    table.querySelectorAll('select.bx').forEach((sel) => sel.addEventListener('change', async () => {
      if (sel.value === '__other') { const was = sel.dataset.was; sel.value = was; typeBoxes(sel.closest('td'), was); return; }
      say.textContent = `Reading box ${sel.value} from every year…`;
      const r = await post('boxes', { ...rowOf(sel), boxes: sel.value });
      if (!r.ok) sel.value = sel.dataset.was;
      changed(r);
    }));
    document.getElementById('ay-key-boxes')?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true; say.textContent = 'Reading those boxes from every year…';
      changed(await post('key-boxes', {}));
    });
    show();
  }

  // adding a row: a document the binder expects, with its box (blank: the usual one)
  const adder = document.getElementById('ay-add');
  if (adder) {
    const open = document.getElementById('ay-add-open');
    open.addEventListener('click', () => {
      adder.hidden = !adder.hidden;
      open.setAttribute('aria-expanded', String(!adder.hidden));
      if (!adder.hidden) adder.querySelector('select, input')?.focus();
    });
    const go = (what, body) => fetch(`/api/matrix/${what}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(async (r) => { if (r.ok) location.reload(); else document.getElementById('ay-say').textContent = (await r.json().catch(() => ({}))).error || 'That didn’t work.'; });
    document.getElementById('ay-add-go')?.addEventListener('click', () => {
      const [source, doc_type] = document.getElementById('ay-doc').value.split('|');
      go('add', { source, doc_type, boxes: document.getElementById('ay-boxes').value.trim() });
    });
    document.getElementById('ay-add-all')?.addEventListener('click', () => go('add-all', {}));
  }

  // -- starting a year: the next one from this one's rows, or an earlier one ---------------------
  const dialogs = (id) => document.getElementById(id);
  const openDlg = (id) => { const d = dialogs(id); d.hidden = false; d.querySelector('input, select, button')?.focus(); };
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { dialogs(b.dataset.close).hidden = true; }));
  document.querySelectorAll('.scrim:not(#viewer)').forEach((s) => s.addEventListener('keydown', (e) => { if (e.key === 'Escape') s.hidden = true; }));
  const start = async (button, year, from, boxes) => {
    const drop = [...document.querySelectorAll(boxes)].filter((c) => !c.checked).map((c) => c.value);
    button.disabled = true;
    const r = await fetch('/api/year/new', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year, from, drop }) });
    if (r.ok) { location.reload(); return; }  // the server moves to the year started
    button.disabled = false;
    button.textContent = (await r.json().catch(() => ({}))).error || 'That didn’t work';
  };
  dialogs('newyear')?.addEventListener('click', () => openDlg('newyear-dlg'));
  dialogs('earlieryear')?.addEventListener('click', () => openDlg('earlier-dlg'));
  dialogs('ny-start')?.addEventListener('click', (e) => {
    const d = dialogs('newyear-dlg').querySelector('.dialog');
    start(e.currentTarget, Number(d.dataset.year), Number(d.dataset.from), '.ny-keep');
  });
  dialogs('ey-start')?.addEventListener('click', (e) => {
    const d = dialogs('earlier-dlg').querySelector('.dialog');
    start(e.currentTarget, Number(dialogs('ey-year').value), Number(d.dataset.from), '.ey-keep');
  });

  // the year's checks run again (the rule book), then the page shows what they found
  document.querySelectorAll('.recheck').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'Checking…';
    const r = await fetch('/api/reconcile', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year: Number(b.dataset.year) }) });
    if (r.ok) location.reload(); else { b.disabled = false; b.textContent = 'Check again'; }
  }));

  const api = (url, body) => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const errorOf = async (r) => (await r.json().catch(() => ({}))).error || 'That didn’t work.';

  // a browser downloads; the app window saves into this Mac's Downloads folder
  async function download(ids, year, say) {
    if (document.body.dataset.remote === 'true') {
      location.href = !year && ids.length === 1 ? `/documents/${encodeURIComponent(ids[0])}/file`
        : `/documents/zip?${year ? `year=${year}` : `ids=${encodeURIComponent(ids.join(','))}`}`;
      return;
    }
    say.textContent = 'Saving…';
    const r = await api('/api/documents/save', year ? { year } : { ids });
    say.textContent = r.ok ? `Saved to Downloads: ${(await r.json()).name}` : await errorOf(r);
  }
  document.querySelectorAll('.dl-year').forEach((b) => b.addEventListener('click', () => {
    b.disabled = true;
    download([], Number(b.dataset.year), b).finally(() => { b.disabled = false; });
  }));

  // -- Expect another document: a row on a year's checklist, from its card -------------
  if (dialogs('add-row')) {
    let year = null;
    const expect = (y) => {
      year = y; dialogs('ar-year').textContent = y; dialogs('ar-say').textContent = '';
      openDlg('add-row');
    };
    document.querySelectorAll('.expect').forEach((b) => b.addEventListener('click', () => expect(Number(b.dataset.year))));
    dialogs('ar-type').addEventListener('change', () => {
      const isNew = dialogs('ar-type').value === '__new__';
      dialogs('ar-newtype').hidden = !isNew;
      if (isNew) dialogs('nt-name').focus();
    });
    dialogs('ar-add').addEventListener('click', async (e) => {
      const b = e.currentTarget, say = dialogs('ar-say');
      b.disabled = true;
      let docType = dialogs('ar-type').value;
      if (docType === '__new__') {  // the new kind first, then the row with it
        const t = await api('/api/doc-types', { name: dialogs('nt-name').value,
          folder: dialogs('nt-folder').value, keywords: dialogs('nt-words').value });
        if (!t.ok) { say.textContent = await errorOf(t); b.disabled = false; return; }
        docType = (await t.json()).id;
      }
      const r = await api('/api/rows', { year, institution: dialogs('ar-inst').value,
        source: dialogs('ar-src').value, doc_type: docType, owner: dialogs('ar-owner').value,
        amount: dialogs('ar-amount').value, comment: dialogs('ar-comment').value });
      if (r.ok) { location.href = `/?year=${year}`; return; }
      say.textContent = await errorOf(r); b.disabled = false;
    });
    // other pages link here with #expect (Check's "Add a row", a return's totals)
    if (location.hash === '#expect') {
      history.replaceState(null, '', location.pathname + location.search);
      expect(Number((document.querySelector('.card.front .expect') || document.querySelector('.expect')).dataset.year));
    }
  }

  // -- For my preparer: papers as they are, each with a note -------------------------------
  if (dialogs('papers')) {
    let year = null;
    const files = dialogs('pp-files'), list = dialogs('pp-list'), go = dialogs('pp-go');
    document.querySelectorAll('.pp-add').forEach((b) => b.addEventListener('click', () => {
      year = Number(b.dataset.year); dialogs('pp-year').textContent = year;
      files.value = ''; list.replaceChildren(); dialogs('pp-say').textContent = ''; go.disabled = true;
      openDlg('papers');
    }));
    files.addEventListener('change', () => {  // a note beside each file: what it's for
      list.replaceChildren(...[...files.files].map((f, i) => {
        const li = document.createElement('li'), label = document.createElement('label');
        const input = document.createElement('input');
        input.id = `pp-note-${i}`; input.maxLength = 120; input.placeholder = 'What it’s for, e.g. FBAR: savings, highest balance';
        label.htmlFor = input.id; label.textContent = f.name;
        li.className = 'field'; li.append(label, input);
        return li;
      }));
      go.disabled = !files.files.length;
    });
    go.addEventListener('click', async () => {
      const say = dialogs('pp-say');
      go.disabled = true;
      for (const [i, f] of [...files.files].entries()) {
        say.textContent = `Adding ${f.name}…`;
        const q = new URLSearchParams({ year, note: dialogs(`pp-note-${i}`).value });
        const r = await fetch(`/api/preparer?${q}`, { method: 'POST', body: f, headers: {
          'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(f.name) } });
        if (!r.ok) { say.textContent = `${f.name}: ${await errorOf(r)}`; go.disabled = false; return; }
      }
      location.href = `/?year=${year}`;
    });
    // on the year's panel: edit a note in place; delete with a second click
    document.querySelectorAll('.papers .pp-note').forEach((b) => b.addEventListener('click', () => {
      const li = b.closest('li'), input = document.createElement('input'), save = document.createElement('button');
      input.value = li.dataset.note; input.maxLength = 120; input.setAttribute('aria-label', 'Note');
      save.type = 'button'; save.className = 'btn small'; save.textContent = 'Save';
      save.addEventListener('click', async () => {
        const r = await api('/api/preparer/edit', { id: li.dataset.id, what: 'note', note: input.value });
        if (r.ok) location.reload(); else save.textContent = await errorOf(r);
      });
      li.querySelector('.pp-what').replaceChildren(input, ' ', save);
      input.focus();
    }));
    document.querySelectorAll('.papers .pp-del').forEach((b) => b.addEventListener('click', async () => {
      if (!b.classList.contains('confirm')) {
        b.classList.add('confirm'); b.textContent = 'Delete?';
        setTimeout(() => { b.classList.remove('confirm'); b.textContent = 'Delete'; }, 4000);
        return;
      }
      const r = await api('/api/documents/delete', { id: b.closest('li').dataset.id });
      if (r.ok) location.reload(); else b.textContent = await errorOf(r);
    }));
  }

  // -- the deck of years ------------------------------------------------------------------------
  const deck = document.getElementById('deck');
  if (!deck) return;
  const cards = [...deck.querySelectorAll('.card')];
  const n = cards.length;
  let front = Number(deck.dataset.front), timer = null;
  const yearOf = (c) => Number(c.dataset.year);

  function place() {
    // the year in front, then the others newest first behind it
    const order = [cards.find((c) => yearOf(c) === front),
      ...cards.filter((c) => yearOf(c) !== front).sort((a, b) => yearOf(b) - yearOf(a))];
    const width = deck.clientWidth;
    const step = n > 1 ? Math.min(30, (width * 0.2) / (n - 1)) : 0;  // how far right each card behind sits
    const tab = Math.min(96, (width - step * (n - 1) - 24) / n);
    deck.style.setProperty('--step', `${step}px`);
    deck.style.setProperty('--spread', `${step * (n - 1)}px`);
    deck.style.setProperty('--fan', `${36 * (n - 1) + 40}px`);
    order.forEach((c, p) => {
      const on = p === 0;
      c.classList.toggle('front', on);
      c.style.setProperty('--p', p);
      c.style.zIndex = on ? '' : String(10 - Math.min(p, 9));
      c.querySelector('.body').inert = !on;
      c.querySelector('.body').setAttribute('aria-hidden', String(!on));
      const t = c.querySelector('.tab');
      if (on) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current');
      // oldest on the left, each on its own card's top edge (never out past it)
      t.style.left = `${width - step * (n - 1) - (n - Number(c.dataset.slot)) * tab - 12}px`;
      t.style.width = `${tab - 4}px`;
    });
    // every year's card the same height: what the window has room for below the fan; the
    // front card's grid scrolls inside it
    const top = deck.getBoundingClientRect().top + scrollY + 36 * (n - 1) + 40;
    deck.style.setProperty('--card', `${Math.max(360, innerHeight - top - 24)}px`);
    const h = order[0].offsetHeight;
    deck.style.setProperty('--h', `${h}px`);
    // the lean: the top of each card behind recedes about the same however tall the cards are
    deck.style.setProperty('--tilt', `${Math.min(7, (Math.asin(Math.min(1, 80 / h)) * 180) / Math.PI).toFixed(2)}deg`);
    deck.style.height = `${36 * (n - 1) + 40 + h}px`;
    document.querySelectorAll('[data-side]').forEach((s) => { s.hidden = Number(s.dataset.side) !== front; });
    // the year in front is the year the other pages open on
    document.querySelectorAll('.topnav-links a').forEach((a) => {
      const u = new URL(a.href);
      if (!['/documents', '/check', '/add'].includes(u.pathname)) return;
      u.searchParams.set('year', front); a.href = u.pathname + u.search;
    });
    history.replaceState(history.state, '', `/?year=${front}${location.hash}`);
  }

  function bring(year) {
    if (year === front || timer) return;
    const card = cards.find((c) => yearOf(c) === year);
    closeSquare();
    if (reduced) { front = year; place(); return; }
    // up out of the deck, tipped towards you; then down flat in front
    card.style.setProperty('--from', card.style.getPropertyValue('--p') || '1');
    card.classList.add('lift');
    timer = setTimeout(() => { card.classList.remove('lift'); front = year; place(); timer = null; }, 300);
  }
  deck.addEventListener('click', (e) => {
    const t = e.target.closest('.tab');
    if (t) { bring(Number(t.dataset.year)); return; }
    const sq = e.target.closest('.sq');
    if (sq) showSquare(sq);
  });

  // -- a square's details, in the side panel ----------------------------------------------------
  const panel = document.getElementById('sq');
  let picked = null;
  function showSquare(sq) {
    if (picked) picked.classList.remove('on');
    picked = sq; sq.classList.add('on');
    const d = sq.dataset;
    document.getElementById('sq-head').textContent = `${d.kind} · ${d.year}`;
    document.getElementById('sq-sender').textContent = d.sender;
    document.getElementById('sq-what').textContent = d.what[0].toUpperCase() + d.what.slice(1) + '.';
    document.getElementById('sq-amount').textContent = d.amount;
    const btn = document.getElementById('sq-view');
    btn.hidden = !d.docs;
    btn.dataset.doc = (d.docs || '').split(' ')[0];
    panel.hidden = false;
    loadRows(sq);
  }
  function closeSquare() { if (picked) picked.classList.remove('on'); picked = null; panel.hidden = true; }

  // -- a square's checklist rows: what the checklist did to each, from the panel --------
  const rowsBox = document.getElementById('sq-rows'), say = document.getElementById('sq-say');
  async function loadRows(sq) {
    rowsBox.replaceChildren(); say.textContent = '';
    if (!sq.dataset.rows) return;
    const r = await fetch(`/parts/rows?ids=${encodeURIComponent(sq.dataset.rows)}`);
    if (r.ok && picked === sq) rowsBox.innerHTML = await r.text();  // escaped by the server
  }
  // a change shows on the card too: the page comes back with the same square open
  const again = () => {
    history.replaceState(null, '', `/?year=${front}` + (picked ? `#sq=${encodeURIComponent(picked.dataset.key)}` : ''));
    location.reload();
  };
  const done = async (r) => { if (r.ok) again(); else say.textContent = await errorOf(r); };
  const idOf = (el) => el.closest('[data-id]').dataset.id;
  const rowFile = document.getElementById('rowfile');
  let uploadTo = null;
  rowsBox.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const id = idOf(b), act = b.dataset.act;
    if (act === 'portal') {
      if (document.body.dataset.remote === 'true') { window.open(b.dataset.url, '_blank', 'noopener'); return; }
      const r = await api('/api/open-portal', { url: b.dataset.url });
      say.textContent = r.ok ? 'Portal window opened. Downloads made there are added automatically.' : await errorOf(r);
    } else if (act === 'download') {
      download(b.dataset.ids.split(','), null, say);
    } else if (act === 'upload') {
      uploadTo = b.closest('[data-id]'); rowFile.click();
    } else if (act === 'verify') {
      b.disabled = true; done(await api('/api/verify', { id, verified: b.dataset.verified === 'true' }));
    } else if (act === 'away') {
      const why = b.closest('.xrow').querySelector('.why');
      why.hidden = !why.hidden; b.setAttribute('aria-expanded', String(!why.hidden));
      if (!why.hidden) why.querySelector('input').focus();
    } else if (act === 'not_applicable' || act === 'applicable') {
      const reason = b.closest('.xrow').querySelector('[data-field="reason"]').value;
      b.disabled = true; done(await api('/api/applicability', { id, value: act, reason }));
    } else if (act === 'delete') {  // a second click confirms
      if (!b.classList.contains('confirm')) {
        b.classList.add('confirm'); b.textContent = 'Remove it?';
        setTimeout(() => { b.classList.remove('confirm'); b.textContent = 'Remove the row'; }, 4000);
        return;
      }
      b.disabled = true; done(await api('/api/rows/delete', { id }));
    }
  });
  rowsBox.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || !e.target.matches('input')) return;
    if (e.target.dataset.field === 'reason') e.target.closest('.why').querySelector('button').click();
    else e.target.blur();  // saves it
  });
  rowsBox.addEventListener('change', async (e) => {
    const t = e.target, row = t.dataset.source ? { source: t.dataset.source, doc_type: t.dataset.docType } : null;
    if (t.matches('.pick-box')) {  // the box this row follows, in every year (picked first, key boxes kept)
      const boxes = [t.value, ...t.dataset.boxes.split(',').filter((b) => b && b !== t.value)].join(',');
      say.textContent = `Reading box ${t.value} from every year…`; t.disabled = true;
      done(await api(t.dataset.in ? '/api/matrix/boxes' : '/api/matrix/add', { ...row, boxes }));
      return;
    }
    if (t.dataset.field === 'box') {  // a row's other box, that year alone
      say.textContent = 'Saving…';
      done(await api('/api/matrix/set', { ...row, year: Number(t.closest('.xrow').dataset.year), box: t.dataset.i, value: t.value }));
      return;
    }
    const field = t.dataset.field;
    if (!['amount', 'comment', 'portal'].includes(field)) return;
    say.textContent = 'Saving…';
    const r = await api('/api/rows/edit', { id: idOf(e.target), [field]: e.target.value });
    if (!r.ok) { say.textContent = await errorOf(r); return; }
    if (field === 'comment') say.textContent = 'Saved.'; else again();  // the square shows it
  });
  rowFile.addEventListener('change', async () => {
    const file = rowFile.files[0];
    rowFile.value = '';
    if (!file || !uploadTo) return;
    say.textContent = `Adding ${file.name}…`;
    const q = new URLSearchParams({ target: uploadTo.dataset.id, year: uploadTo.dataset.year });
    done(await fetch(`/api/upload?${q}`, { method: 'POST', body: file, headers: {
      'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) } }));
  });
  // not expected this year (no square shows them): expected again, from the year's panel
  document.querySelectorAll('.away .again').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    const r = await api('/api/applicability', { id: idOf(b), value: 'applicable' });
    if (r.ok) location.reload(); else { b.disabled = false; b.textContent = await errorOf(r); }
  }));
  document.getElementById('sq-close').addEventListener('click', closeSquare);

  // -- Noticed for you: one click answers a note (an undoable change); Show me finds its square --
  document.querySelectorAll('[data-notice]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    const r = await fetch('/api/notice', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: b.dataset.notice, action: b.dataset.action }),
    });
    if (r.ok) location.reload(); else { b.disabled = false; b.textContent = await errorOf(r); }
  }));
  document.querySelectorAll('[data-show]').forEach((b) => b.addEventListener('click', () => {
    const sq = deck.querySelector(`.card.front .sq[data-key="${CSS.escape(b.dataset.show)}"]`);
    if (!sq) return;
    sq.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
    showSquare(sq);
    sq.focus({ preventScroll: true });
  }));
  document.getElementById('sq-view').addEventListener('click', (e) => view(e.currentTarget.dataset.doc));

  document.querySelectorAll('.grid').forEach((g) => g.style.setProperty('--cols', g.dataset.cols));
  addEventListener('resize', place);
  place();
  const back = location.hash.match(/^#sq=(.+)$/);  // after a change from the panel
  if (back) {
    const sq = deck.querySelector(`.card.front .sq[data-key="${CSS.escape(decodeURIComponent(back[1]))}"]`);
    if (sq) { showSquare(sq); sq.focus({ preventScroll: true }); }
  }
})();
