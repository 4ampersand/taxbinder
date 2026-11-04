/* Add documents as a factory. Files go to the reader's queue (/api/add) one by one;
   the reader takes each document through every step, the model's included, and
   /api/add/status says how far each got (`steps`) and what each step found (`found`). Steps the
   reader finishes together are shown one by one, at most one station per TICK, so each can be
   seen; with Reduce motion there is no belt and the cards list the steps at once. Below the belt,
   the reading bench shows one document's two readings, what's kept and its checks
   (/bench/<id>): the one being read, or the one whose name was clicked. */
(() => {
  const fx = document.getElementById('fx');
  if (!fx) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TICK = 280; // ms per station, at the least
  const COMPACT = 12; // more documents than this: only the one being read is on the belt
  const STATIONS = [...document.querySelectorAll('#fx-stations [data-step]')].map((li) => li.dataset.step);
  const END = STATIONS.length - 1; // Filed (or Needs you)
  const NAMES = Object.fromEntries([...document.querySelectorAll('#fx-stations [data-step]')]
    .map((li) => [li.dataset.step, li.querySelector('b').textContent]));
  const $ = (id) => document.getElementById(id);
  const belt = $('fx-belt'), cardsBox = $('fx-cards');

  let st = { total: 0, done: 0, items: [], running: false };
  let shown = []; // per item: how many of its steps are on show (one more than its steps: gone)
  let sending = false, timer = null, ticker = null;
  let atStation = { where: '', since: 0 }; // the document being read: its station, since when
  let qs = null; // the questions below the belt: { left, ready, docs } from /parts/asks
  let asksAt = ''; // the run they were loaded for
  let filedNow = 0; // how many "Looks good, file them" just filed

  // -- receiving files ------------------------------------------------------------------------
  async function send(files) {
    const batch = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    sending = true; poll();
    for (const file of files) {
      const r = await fetch('/api/add', {
        method: 'POST', body: file,
        headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name), 'X-Batch': batch },
      }).catch(() => null);
      if (!r || !r.ok) { tell('TaxBinder stopped answering: add the rest again later.'); break; }
    }
    sending = false; poll();
  }
  const exts = new Set(($('fx-drop')?.dataset.exts || '').split(','));
  const visible = (files) => files.filter((f) => !f.name.startsWith('.')  // not .DS_Store
    && exts.has(f.name.split('.').pop().toLowerCase()));  // a folder: only what Add takes
  const fileOf = (entry) => new Promise((res, rej) => entry.file(res, rej));
  async function walk(entry, out) { // everything inside a dropped folder
    if (entry.isFile) { out.push(await fileOf(entry)); return; }
    const reader = entry.createReader();
    for (;;) {
      const some = await new Promise((res, rej) => reader.readEntries(res, rej));
      if (!some.length) break;
      for (const child of some) await walk(child, out);
    }
  }
  const tell = (text) => { $('fx-say').textContent = text; $('fx-say').classList.remove('vh'); }; // always shown
  const zone = $('fx-drop');
  $('fx-pick').addEventListener('click', () => $('fx-files').click());
  $('fx-files').addEventListener('change', (e) => { send([...e.target.files]); e.target.value = ''; });
  if ($('fx-pick-folder')) {
    $('fx-pick-folder').addEventListener('click', () => $('fx-folder').click());
    $('fx-folder').addEventListener('change', (e) => { send(visible([...e.target.files])); e.target.value = ''; });
  }
  // dropped anywhere on the page (once reading starts the drop area folds
  // into a slim bar, and a drop needn't aim for it)
  document.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) zone.classList.remove('over'); });
  document.addEventListener('drop', async (e) => {
    e.preventDefault(); zone.classList.remove('over');
    const entries = [...(e.dataTransfer.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry());
    if (!entries.length || entries.some((x) => !x) || entries.every((x) => x.isFile)) { send([...e.dataTransfer.files]); return; }
    const files = [];
    try { for (const entry of entries) await walk(entry, files); } catch { tell('That folder couldn’t be read.'); return; }
    send(visible(files));
  });
  if ($('fx-demo')) $('fx-demo').addEventListener('click', async () => {
    sending = true; poll();
    await fetch('/api/demo/add', { method: 'POST' }).catch(() => null);
    sending = false; poll();
  });

  // -- following the reader -------------------------------------------------------------------
  async function poll() {
    clearTimeout(timer);
    const r = await fetch('/api/add/status').catch(() => null);
    if (r && r.ok) {
      const next = await r.json();
      const same = next.items.length >= st.items.length && st.items.every((x, i) => next.items[i] && next.items[i].name === x.name);
      if (!same) shown = []; // a new batch
      st = next;
      st.items.forEach((x, i) => {
        if (shown[i] === undefined) shown[i] = reduced || !st.running && !sending ? gone(x) : 1;
      });
      if (!ticker) step();
    }
    if (st.running || sending) timer = setTimeout(poll, 500);
  }
  const finished = (x) => x.state !== 'queued' && x.state !== 'reading';
  const gone = (x) => x.steps.length + (finished(x) ? 1 : 0);
  // one station further for the first document that's behind, then draw
  function step() {
    ticker = null;
    const i = st.items.findIndex((x, n) => shown[n] < gone(x));
    if (i >= 0) {
      if (reduced) st.items.forEach((x, n) => { shown[n] = gone(x); });
      else shown[i] += 1;
    }
    draw();
    if (st.items.some((x, n) => shown[n] < gone(x))) ticker = setTimeout(step, TICK);
  }

  // where a document is: its station's index, -1 waiting, END + 1 gone off the belt
  function placeOf(x, n) {
    const k = shown[n];
    if (x.state === 'bad') return END + 1;
    if (k > x.steps.length) return END + 1;
    const last = x.steps[k - 1];
    if (x.state === 'queued' || k <= 1) return x.state === 'queued' ? -1 : 1;
    if (last === 'reading') return 1;
    if (last === 'filed' || last === 'asked') return END;
    const at = STATIONS.indexOf(last);
    const caughtUp = k === x.steps.length && !finished(x);
    return caughtUp ? at + 1 : at; // still working: at the next station
  }

  // something on it will be asked, once the step that found it is on show
  const asking = (x, n) => (x.found.asks || []).length > 0 && x.steps.slice(0, shown[n]).includes('whose');
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  function modelSays(m) {
    if (!m) return null;
    if (m.state === 'read') return `${plural(m.boxes, 'box', 'boxes')} read · ${m.agreed} agreed with the rules` +
      (m.differ ? ` · ${m.differ} differ: you’ll be asked` : '') + (m.seconds ? ` · ${m.seconds}s` : '');
    return { 'no model': 'no AI model in use: the rules read it alone',
      'not needed': 'not a form the AI model reads',
      failed: 'couldn’t read it now; it tries again later' }[m.state] || m.state;
  }
  // the card's lines: each step's finding, once that step is on show
  function facts(x, n) {
    const f = x.found, k = shown[n], has = (s) => x.steps.slice(0, k).includes(s), out = [];
    if (has('reading')) out.push(['Reading', f.scan ? 'a scan or photo: its words read on this Mac' : 'its text']);
    if (has('recognised')) out.push(['It is', f.kind || 'not recognised: you’ll be asked']);
    if (has('sender')) out.push(['Sender', f.sender ? f.sender + (f.sender_by === 'its row' ? ' (from the row it matched)' : '') : 'not read: you’ll be asked']);
    if (has('whose')) {
      const whose = (f.whose || []).length ? f.whose.join(' & ') + (f.whose_by === 'its row' ? ' (from its row)' : '')
        : (f.asks || []).includes('whose it is') ? 'unclear: you’ll be asked' : 'no name printed';
      out.push(['Whose & year', `${whose} · ${f.year || 'year not read: you’ll be asked'}`]);
    }
    if (has('amounts')) out.push(['Amounts', f.amounts ? `${plural(f.amounts, 'box', 'boxes')} for its row in Across the years` + (f.suggested ? `, ${f.suggested} new` : '')
      : 'none for Across the years yet: no row of it takes a box (the bench shows what was read)']);
    if (has('model')) out.push(['The AI model', modelSays(f.model)]);
    else if (placeOf(x, n) === STATIONS.indexOf('model')) {
      const secs = Math.floor((Date.now() - atStation.since) / 1000);
      out.push(['The AI model', `reading the page…${secs >= 2 ? ` ${secs} s` : ''}`, 'think']);
    }
    if (has('checks')) out.push(['Checks', (f.checks || {}).problem ? 'its year can’t be checked yet'
      : f.year ? `${f.year} checked again, with this document in it` : 'no year to check']);
    if (has('asked')) out.push(['Needs you', (f.asks || []).join(', '), 'ask']);
    if (has('filed')) out.push([filedNow ? 'Filed' : 'Goes', `into ${f.year || 'its year'}` + (filedNow ? '' : ', once you file them')]);
    return out;
  }
  function badge(x, n) {
    if (x.state === 'bad') return ['Couldn’t add', 'bad'];
    if (x.state === 'duplicate') return ['Already added', ''];
    const at = placeOf(x, n);
    if (at < 0) return ['Waiting', ''];
    const end = at >= END && finished(x);
    if (end && filedNow) return ['Filed', 'ok'];
    if (end && qs && !qs.docs.has(x.found.doc)) return [x.steps.includes('asked') ? 'Answered' : 'Ready to file', 'ok'];
    if (x.steps[shown[n] - 1] === 'asked' || at > END && x.steps.includes('asked')) return ['Needs you', 'ask'];
    if (at >= END) return ['Ready to file', 'ok'];
    return [NAMES[STATIONS[at]] + '…', 'on'];
  }

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const cardEls = new Map(); // item index → its card
  function draw() {
    fx.hidden = !st.items.length;
    // while there are documents: the drop area and the words above fold away and the steps
    // slide up into their place (app.css), so the steps and the questions are in sight
    document.body.classList.toggle('fx-on', st.items.length > 0);
    const n = st.items.length, done = st.items.filter((x, i) => placeOf(x, i) >= END).length;
    const endedAt = (end) => st.items.filter((x, i) => placeOf(x, i) >= END && x.steps.includes(end)).length;
    const filed = endedAt('filed'), asked = endedAt('asked');
    const bad = st.items.filter((x) => x.state === 'bad').length, copies = st.items.filter((x) => x.state === 'duplicate').length;
    const busy = st.items.findIndex((x, i) => { const p = placeOf(x, i); return p >= 0 && p < END; });
    const ended = n && busy < 0 && !st.running && !sending;
    if (ended && `${n}|${done}` !== asksAt) { asksAt = `${n}|${done}`; loadAsks(); } // its questions, here
    const left = qs ? qs.left : asked; // what's still to answer, once the questions are here
    const said = filedNow ? [`${plural(filedNow, 'document', 'documents')} filed`] : [
      filed + asked ? `${filed + asked} read` : '',
      left ? `${plural(left, 'question', 'questions')} for you` : qs && qs.ready ? 'ready to file' : ''];
    said.push(copies ? `${plural(copies, 'copy', 'copies')} skipped` : '', bad ? `${bad} couldn’t be added` : '');
    const head = said.filter(Boolean).join(', ');
    $('fx-head').textContent = !n ? 'Add your tax documents' : !ended ? `Reading ${Math.min(done + 1, n)} of ${n}`
      : head[0].toUpperCase() + head.slice(1);
    const now = busy >= 0 ? st.items[busy] : null;
    const say = now ? `Reading ${now.name}` : n ? $('fx-head').textContent : '';
    if ($('fx-say').textContent !== say) $('fx-say').textContent = say; // once per document, not per station
    $('fx-say').classList.toggle('vh', !!now || say === $('fx-head').textContent); // heard; the belt shows it

    // the stations: lit where the document being read is, with how long it has been there
    // (the model can take a while on a page, and it must show it's working)
    const warns = now && asking(now, busy);
    const where = now ? `${busy}|${placeOf(now, busy)}` : '';
    if (where !== atStation.where) atStation = { where, since: Date.now() };
    const secs = Math.floor((Date.now() - atStation.since) / 1000);
    document.querySelectorAll('#fx-stations .st').forEach((li, s) => {
      const here = !!now && placeOf(now, busy) === s;
      li.classList.toggle('on', here && !warns);
      li.classList.toggle('warn', here && warns);
      li.querySelector('.tm').textContent = here && !warns && secs >= 2 ? `working · ${secs} s` : '';
    });
    document.body.classList.toggle('fx-busy', !!now); // the busy pointer while one is worked on

    // the belt: each document at its station; a big drop keeps only the one being read on it
    belt.classList.toggle('run', busy >= 0);
    belt.classList.toggle('idle', busy < 0 && !sending);  // nothing on it: a thin strip
    const compact = n > COMPACT;
    const onBelt = st.items.map((x, i) => [x, i]).filter(([x, i]) => {
      const p = placeOf(x, i);
      return x.state !== 'bad' && (!compact || (p >= 0 && p <= END && i === busy));
    });
    const keep = new Set(onBelt.map(([, i]) => String(i)));
    [...belt.querySelectorAll('.doc')].forEach((d) => { if (!keep.has(d.dataset.i)) d.remove(); });
    let waiting = 0; // the ones waiting, stacked at the start
    for (const [x, i] of onBelt) {
      let d = belt.querySelector(`.doc[data-i="${i}"]`);
      if (!d) {
        d = el('div', 'doc'); d.dataset.i = i;
        for (let r = 0; r < 6; r++) d.append(el('i'));
        d.append(el('span', 'scan'), el('span', 'stamp'));
        belt.append(d);
      }
      const p = placeOf(x, i);
      d.style.setProperty('--x', p > END ? 1.1 : p < 0 ? 0 : (p + 0.5) / STATIONS.length);
      d.style.setProperty('--n', String(p < 0 ? waiting++ : 0));
      d.classList.toggle('wait', p < 0);
      d.classList.toggle('out', p > END);
      d.classList.toggle('scanning', p === 1 && !!x.found.scan);
      const kind = x.steps.slice(0, shown[i]).includes('recognised') ? (x.found.kind || '?') : '';
      const stamp = d.querySelector('.stamp');
      stamp.textContent = kind; stamp.hidden = !kind;
      stamp.classList.toggle('ask', asking(x, i));
    }
    let pile = belt.querySelector('.pile');
    if (compact) {
      if (!pile) { pile = el('div', 'pile'); belt.append(pile); }
      const wait = st.items.filter((x, i) => placeOf(x, i) < 0).length;
      pile.textContent = `${wait} waiting · ${done} done`;
    } else if (pile) pile.remove();

    // the cards: bad files first, then in the order they came
    const order = [...st.items.keys()].sort((a, b) => (st.items[b].state === 'bad') - (st.items[a].state === 'bad') || a - b);
    order.forEach((i, pos) => {
      const x = st.items[i];
      let c = cardEls.get(i);
      if (!c || c.dataset.name !== x.name) {
        c = el('article', 'fx-card'); c.dataset.name = x.name; cardEls.set(i, c);
        const name = x.state === 'bad' ? el('b', 'name', x.name) : el('button', 'name pick', x.name);
        if (x.state !== 'bad') {
          name.type = 'button';
          name.setAttribute('aria-label', `Show ${x.name} at the reading bench`);
          name.addEventListener('click', () => { picked = i; draw(); $('bench').scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' }); });
        }
        const head = el('header'); head.append(name, el('a', 'badge'));
        c.append(head, el('dl'));
      }
      const [word, cls] = badge(x, i);
      const b = c.querySelector('.badge'); b.textContent = word; b.className = 'badge ' + cls;
      const open = qs && qs.docs.has(x.found.doc); // its question is below: the badge leads there
      if (open) { b.href = `#ask-${x.found.doc}`; b.textContent = 'Needs you: answer below'; b.className = 'badge ask'; } else b.removeAttribute('href');
      c.classList.toggle('now', i === busy);
      c.classList.toggle('focus', i === picked);
      const lines = x.state === 'bad' ? [['Why', x.note]] : x.state === 'duplicate' ? [['Skipped', 'the same file is in your binder already']] : facts(x, i);
      const sig = JSON.stringify(lines);
      if (c.dataset.sig !== sig) {
        c.dataset.sig = sig;
        c.querySelector('dl').replaceChildren(...lines.flatMap(([k, v, cl]) => { const dt = el('dt', null, k), dd = el('dd', cl || null, v); return [dt, dd]; }));
      }
      if (cardsBox.children[pos] !== c) cardsBox.insertBefore(c, cardsBox.children[pos] || null);
    });
    while (cardsBox.children.length > order.length) cardsBox.lastChild.remove();
    drawBench(busy);
  }

  // -- questions for you: Check's questions about what's waiting, answered
  // here once the reading ends, each through the same API as Check (undoable); then filing -----
  const asksBox = $('fx-asks');
  async function loadAsks() {
    const r = await fetch('/parts/asks').catch(() => null);
    if (!r || !r.ok) return;
    asksBox.innerHTML = await r.text(); // the server's page: every value escaped there
    const d = asksBox.firstElementChild;
    qs = { left: Number(d.dataset.left), ready: Number(d.dataset.ready), docs: new Set(d.dataset.docs.split(' ').filter(Boolean)) };
    asksBox.hidden = !qs.left && !qs.ready;
    asksBox.querySelectorAll('.ask').forEach((a) => { // the bench holds this run's documents only
      const b = a.querySelector('[data-a="bench"]');
      if (b) b.hidden = !st.items.some((x) => x.found.doc === a.dataset.id);
    });
    draw();
  }
  const post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => null);
  const problem = async (r) => { tell(r ? (await r.json().catch(() => ({}))).error || 'That didn’t work.' : 'TaxBinder stopped answering.'); };
  function filedAll(j) { // what's next, once filed
    filedNow = j.filed || 0;
    const h = el('h2', null, `${plural(filedNow, 'document', 'documents')} filed`); h.id = 'ask-head';
    const go = el('a', 'btn', 'See them in your binder'); go.href = '/';
    const row = el('div', 'row wrap'); row.append(go);
    if (j.go === '/backup') { const b = el('a', 'btn ghost', 'Keep them safe: back up'); b.href = '/backup'; row.append(b); }
    asksBox.replaceChildren(h, el('p', 'muted', 'Undo in Changes puts them back to wait here.'), row);
    asksBox.hidden = false; qs = { left: 0, ready: 0, docs: new Set() };
    draw();
  }
  if (asksBox) {
    asksBox.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-a]');
      if (!b) return;
      const card = b.closest('.ask'), id = card ? card.dataset.id : null, a = b.dataset.a;
      const typed = () => (b.closest('.abox, .ask-box').querySelector('.pick') || {}).value?.trim() || '';
      if (a === 'bench') {
        const i = st.items.findIndex((x) => x.found.doc === id);
        if (i >= 0) { picked = i; draw(); $('bench').scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' }); }
        return;
      }
      let r;
      b.disabled = true;
      if (a === 'file') r = await post('/api/check/confirm', {});
      else if (a === 'whose') r = await post('/api/documents/whose', { id, taxpayers: b.dataset.v.split(',') });
      else if (a === 'mark') r = await post('/api/check/mark', { id, what: b.dataset.v });
      else if (a === 'drop') r = await post('/api/documents/delete', { id });
      else if (a === 'return') r = await post('/api/returns/edit', { id, status: b.dataset.v });
      else if (a === 'type' || a === 'year' || a === 'issuer') {
        const v = typed(), field = { type: 'doc_type', year: 'tax_year', issuer: 'issuer' }[a];
        if (!v) { b.disabled = false; tell(a === 'type' ? 'Choose what it is first.' : 'Type it first.'); return; }
        r = await post('/api/check/edit', { id, [field]: v });
      } else if (a === 'use-model') {
        const body = { id };
        if (b.dataset.issuer) body.issuer = b.dataset.issuer;
        if (b.dataset.year) body.tax_year = b.dataset.year;
        r = await post('/api/check/edit', body);
      } else if (a === 'box' || a === 'box-typed') {
        const v = a === 'box' ? b.dataset.v : typed();
        if (!v) { b.disabled = false; tell('Type the amount first.'); return; }
        r = await post('/api/check/box', { id, box: b.closest('.abox').dataset.box, value: v });
      }
      if (!r || !r.ok) { b.disabled = false; await problem(r); return; }
      if (a === 'file') { filedAll(await r.json()); return; }
      tell('Saved. Every answer can be undone in Changes.');
      loadAsks();
    });
    asksBox.addEventListener('keydown', (e) => { // Enter in a box saves it
      if (e.key !== 'Enter' || !e.target.matches('.pick')) return;
      e.preventDefault();
      const next = e.target.nextElementSibling;
      if (next && next.dataset.a) next.click();
    });
  }

  // -- the reading bench --------------------------------------------------------------------------
  let picked = null; // a card's index, once its name is clicked
  let drawn = ''; // what the bench shows now
  const benches = new Map(); // "<doc>|<steps on show>" → what /bench said
  const reached = (x, n, s) => x.steps.slice(0, shown[n]).includes(s);
  function benchFor(x, n) { // the bench's data for the steps on show; fetched once per stage
    const stage = reached(x, n, 'checks') ? 'checks' : reached(x, n, 'model') ? 'model' : 'amounts';
    const key = `${x.found.doc}|${stage}`;
    if (!benches.has(key)) {
      benches.set(key, null);
      fetch(`/bench/${encodeURIComponent(x.found.doc)}`).then((r) => r.ok ? r.json() : null)
        .then((b) => { benches.set(key, b); draw(); }).catch(() => {});
    }
    return benches.get(key);
  }
  function lines(box, rows, empty) {
    box.replaceChildren(...(rows.length ? rows : [el('p', 'none', empty)]));
  }
  function line(r, cls, note) {
    const d = el('div', 'ln' + (cls ? ' ' + cls : ''));
    d.append(el('span', null, `Box ${r.box}` + (r.label ? ` · ${r.label}` : '')), el('b', null, r.value));
    if (note) d.append(el('small', null, note));
    return d;
  }
  function drawBench(busy) {
    const auto = busy >= 0 && reached(st.items[busy], busy, 'amounts') ? busy
      : [...st.items.keys()].reverse().find((n) => st.items[n].found.doc && reached(st.items[n], n, 'amounts'));
    const n = picked !== null && st.items[picked] ? picked : auto;
    const x = n === undefined || n < 0 ? null : st.items[n];
    $('bench').hidden = !x;
    if (!x) return;
    const sig = [n, shown[n], x.state, [...benches.values()].filter(Boolean).length].join('|');
    if (sig === drawn) return; // nothing new: leave it be (a redraw restarts its animation)
    drawn = sig;
    $('bn-head').textContent = `At the reading bench: ${x.name}`;
    if (!x.found.doc || !reached(x, n, 'amounts')) {
      for (const id of ['bn-rules', 'bn-model', 'bn-kept', 'bn-checks']) lines($(id), [], x.found.doc || finished(x) ? 'Nothing to read: it wasn’t added.' : 'Waiting for its turn.');
      return;
    }
    const b = benchFor(x, n);
    if (!b) { lines($('bn-rules'), [], 'Reading…'); return; }
    const form = !b.rules.length && !b.kept.length;
    lines($('bn-rules'), b.rules.map((r) => line(r, r.check ? 'ask' : '', r.check)),
      form ? 'Not a form read box by box: its sender, whose and year are what count.' : 'Nothing read in its boxes.');
    const m = x.found.model;
    if (!reached(x, n, 'model')) lines($('bn-model'), [el('p', 'none think', 'reading the page…')]);
    else if (b.model.length) lines($('bn-model'), b.model.map((r) => line(r, '', r.says)));
    else lines($('bn-model'), [], m && m.state === 'no model' ? 'No AI model in use: the rules read it alone. Settings can download one.'
      : m && m.state === 'failed' ? 'It couldn’t read it just now; it tries again later.'
        : form || (m && m.state === 'not needed') ? 'Not a form the AI model reads.' : 'Nothing read.');
    // a key box in doubt needs a look; any other is shown for reference, asking nothing
    lines($('bn-kept'), b.kept.map((r) => line(r, !r.key ? 'ref' : r.why ? 'ask' : 'ok',
      !r.key ? (r.why ? 'for reference: ' + r.why : 'for reference') : r.why ? 'needs a look: ' + r.why : 'taken')),
      form ? 'Kept as a document: no boxes to take.' : 'Nothing to take yet.');
    if (!reached(x, n, 'checks')) { lines($('bn-checks'), [], 'Checks run once the amounts are in.'); return; }
    lines($('bn-checks'), b.checks.map((c) => {
      const d = el('div', 'rk');
      d.append(el('span', 'mark ' + c.severity, c.severity === 'error' ? '!' : '?'), el('span', null, c.message), el('code', null, c.rule));
      return d;
    }), 'Nothing in the rule book flags this document.');
  }
  // -- what doesn't come in the mail: None this year, Not sure, or Change (each answer an
  // undoable change, kept for the year; Add goes to where it's added) ---------------------------
  const mail = $('mail');
  if (mail) mail.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-answer]');
    if (!b) return;
    const row = b.closest('.mrow'), value = b.dataset.answer;
    b.disabled = true;
    const r = await fetch('/api/results/answer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year: Number(mail.dataset.year), kind: row.dataset.kind, value }),
    }).catch(() => null);
    b.disabled = false;
    const said = row.querySelector('.said');
    if (!r || !r.ok) { said.hidden = false; said.textContent = 'That didn’t save: try again.'; return; }
    said.textContent = b.dataset.said || '';
    said.hidden = !value;
    row.querySelector('.mk').classList.toggle('asked', !!value);
    row.querySelectorAll('.mbtns > :not(.said)').forEach((x) => { x.hidden = (x.dataset.answer === '') === !value; });
    (value ? row.querySelector('[data-answer=""]') : row.querySelector('[data-answer="no"]')).focus();
  });

  poll();
})();
