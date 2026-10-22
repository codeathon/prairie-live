const $ = (id) => document.getElementById(id);
const picked = new Set();
let lastRanked = [];

async function jget(url) {
  const r = await fetch(url);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function setLight(st) {
  const el = $("light");
  el.className = "light";
  if (st.up && st.pv) el.classList.add("on");
  else if (st.up) el.classList.add("warn");
  else el.classList.add("off");
  $("relay_detail").textContent = st.detail || "";
}

function fillSelect(sel, items, valueKey, labelFn, current) {
  sel.innerHTML = "";
  for (const it of items) {
    const o = document.createElement("option");
    o.value = typeof it === "string" ? it : it[valueKey];
    o.textContent = typeof it === "string" ? it : labelFn(it);
    sel.appendChild(o);
  }
  if (current) sel.value = current;
}

function showXmlFields(fields) {
  const dl = $("xml_fields");
  dl.innerHTML = "";
  const rows = [
    ["points", fields.n_points],
    ["duration (ms)", fmt(fields.duration_ms)],
    ["inter-point delay (ms)", fmt(fields.inter_point_delay_ms)],
    ["initial delay (ms)", fmt(fields.initial_delay_ms)],
    ["spiral", fields.spiral ? "on" : "off"],
    ["spiral revolutions", fmt(fields.spiral_revolutions)],
    ["spiral size (µm)", fmt(fields.spiral_size_um)],
    ["FOV width (µm)", fmt(fields.fov_width_um)],
    ["FOV height (µm)", fmt(fields.fov_height_um)],
    ["pixel size (µm)", fmt(fields.pixel_size_um)],
    ["repetitions", fields.repetitions],
    ["laser", fields.laser || "—"],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v == null ? "—" : v;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  $("fov_note").textContent = fields.fov_note || "";
}

function fmt(v) {
  if (v == null || v === "") return null;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
}

async function refreshRelay() {
  const addr = $("via_relay").value.trim();
  try {
    setLight(await jget("/api/relay-status?addr=" + encodeURIComponent(addr)));
    await jpost("/api/live-addr", { addr });
  } catch (e) {
    setLight({ up: false, pv: false, detail: String(e) });
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";
let marksOn = true;
let marksNote = "";

function applyMarksVisibility() {
  $("live_marks").classList.toggle("hidden", !marksOn);
  // Keep the note's height when hiding marks so the FOV does not jump.
  const note = $("marks_note");
  note.textContent = marksNote;
  note.classList.toggle("dimmed", !marksOn);
  const btn = $("marks_toggle");
  btn.setAttribute("aria-pressed", marksOn ? "true" : "false");
  btn.title = marksOn ? "Hide Mark Points overlay" : "Show Mark Points overlay";
}

async function refreshPoints() {
  let res;
  try {
    res = await jget("/api/points");
  } catch (_) {
    return;
  }
  const svg = $("live_marks");
  const [w, h] = res.frame_wh || [1, 1];
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.replaceChildren();
  const unit = Math.min(w, h);
  for (const p of res.points || []) {
    const cx = p.fx * w;
    const cy = p.fy * h;
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", cx);
    c.setAttribute("cy", cy);
    c.setAttribute("r", Math.max((p.r || 0) * w, unit * 0.012));
    c.setAttribute("stroke-width", unit * 0.004);
    const t = document.createElementNS(SVG_NS, "text");
    t.setAttribute("x", cx + unit * 0.016);
    t.setAttribute("y", cy - unit * 0.012);
    t.setAttribute("font-size", unit * 0.03);
    t.setAttribute("stroke-width", unit * 0.006);
    t.textContent = p.id;
    svg.append(c, t);
  }
  marksNote = res.note || "";
  applyMarksVisibility();
}

async function refreshLive() {
  try {
    const r = await fetch("/api/live.png?t=" + Date.now());
    if (!r.ok) return;
    const blob = await r.blob();
    const img = $("live_img");
    const old = img.src;
    img.src = URL.createObjectURL(blob);
    if (old && old.startsWith("blob:")) URL.revokeObjectURL(old);
  } catch (_) {}
}

// Pick the column count that gives the largest square tile with no scrolling.
function fitGrid() {
  const grid = $("fov_grid");
  const n = grid.children.length || 1;
  const W = grid.clientWidth;
  const H = grid.clientHeight;
  const chrome = 56; // tile title + live caption band + padding
  let best = 1;
  let bestSide = 0;
  for (let c = 1; c <= n; c++) {
    const r = Math.ceil(n / c);
    const side = Math.min(W / c, H / r - chrome);
    if (side > bestSide) {
      bestSide = side;
      best = c;
    }
  }
  grid.style.setProperty("--cols", String(best));
}

function ensurePowerTiles(boards) {
  const grid = $("fov_grid");
  const have = new Set();
  for (const el of grid.querySelectorAll("[data-power]")) have.add(el.dataset.power);
  for (const b of boards || []) {
    const key = String(b.power);
    if (!have.has(key)) {
      const tile = document.createElement("div");
      tile.className = "tile";
      tile.dataset.power = key;
      tile.innerHTML = `<h3></h3><img alt="power ${key}" /><p class="muted"></p>`;
      grid.appendChild(tile);
      fitGrid();
    }
    const tile = grid.querySelector(`[data-power="${key}"]`);
    tile.querySelector("h3").textContent = b.title || `power ${key}`;
    if (b.ready) {
      const img = tile.querySelector("img");
      img.src = `/api/board.png?power=${encodeURIComponent(key)}&t=${Date.now()}`;
      tile.querySelector("p").textContent = "updated";
    } else {
      tile.querySelector("p").textContent = "waiting for first trial…";
    }
  }
}

function powerListFromBox() {
  return ($("powers").value || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number(s))
    .filter((n) => !Number.isNaN(n));
}

function syncTypedPowers() {
  const want = powerListFromBox().map((p) => String(p));
  const keep = new Set(want);
  for (const el of $("fov_grid").querySelectorAll("[data-power]")) {
    if (!keep.has(el.dataset.power) && !el.querySelector("img").getAttribute("src")) el.remove();
  }
  ensurePowerTiles(want.map((p) => ({ power: p, title: `power ${p}`, ready: false })));
  fitGrid();
}

async function onXmlFile(ev) {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  const b64 = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = reject;
    r.readAsDataURL(f);
  });
  const res = await jpost("/api/parse-xml", {
    name: f.name,
    content_b64: b64,
    powers: $("powers").value,
  });
  if (!res.ok) {
    $("xml_path").textContent = res.error || "parse failed";
    return;
  }
  $("xml_path").textContent = res.path;
  $("plan").textContent = res.plan || "";
  showXmlFields(res.fields || {});
  refreshPoints();
  if (res.fields && res.fields.repetitions) $("repetitions").value = res.fields.repetitions;
  syncTypedPowers();
}

function renderCandidates(ranked) {
  lastRanked = ranked || [];
  const box = $("cand_box");
  const tb = $("cand_table").querySelector("tbody");
  tb.innerHTML = "";
  if (!lastRanked.length) {
    box.hidden = true;
    $("side").classList.remove("has-cands");
    return;
  }
  box.hidden = false;
  $("side").classList.add("has-cands");
  if (!picked.size) {
    lastRanked.filter((r) => r.stimulated).forEach((r) => picked.add(String(r.id)));
  }
  for (const r of lastRanked) {
    const tr = document.createElement("tr");
    const id = String(r.id);
    if (picked.has(id)) tr.classList.add("picked");
    tr.innerHTML = `<td>${picked.has(id) ? "●" : "○"}</td><td>${id}</td><td>${r.min_power ?? "—"}</td><td>${r.dff == null ? "—" : Number(r.dff).toFixed(3)}</td><td>${r.tercile}</td>`;
    tr.addEventListener("click", () => {
      if (picked.has(id)) picked.delete(id);
      else picked.add(id);
      renderCandidates(lastRanked);
    });
    tb.appendChild(tr);
  }
}

async function tick() {
  const st = await jget("/api/status");
  $("log").textContent = (st.log || []).join("\n");
  $("log").scrollTop = $("log").scrollHeight;
  $("run").disabled = !!st.running;
  $("stop").disabled = !st.running;
  if (st.has_candidates) renderCandidates(st.ranked);
  if (st.error) $("save_status").textContent = st.error;
  if (st.live) $("live_cap").textContent = st.live.detail || "";
  if (st.boards && st.boards.length) {
    // Before a run the Powers box decides which tiles exist.
    const typed = new Set(powerListFromBox().map((p) => String(p)));
    ensurePowerTiles(st.running ? st.boards : st.boards.filter((b) => b.ready || typed.has(String(b.power))));
  }
}

async function boot() {
  const d = await jget("/api/defaults");
  $("via_relay").value = d.via_relay;
  $("powers").value = d.powers;
  $("seed").value = d.seed;
  $("repetitions").value = d.repetitions;
  $("stim_dff_min").value = d.stim_dff_min;
  fillSelect($("serial"), d.com_ports, "device", (p) => `${p.device} — ${p.description}`, d.serial);
  fillSelect($("stim_mode"), d.stim_modes, null, null, d.stim_mode);
  fillSelect($("trigger"), d.triggers, null, null, d.trigger);
  setLight(d.relay || {});
  $("via_relay").addEventListener("change", refreshRelay);
  $("powers").addEventListener("input", syncTypedPowers);
  $("xml_file").addEventListener("change", onXmlFile);
  $("marks_toggle").addEventListener("click", () => {
    marksOn = !marksOn;
    applyMarksVisibility();
  });
  applyMarksVisibility();
  $("run").addEventListener("click", async () => {
    const res = await jpost("/api/run", {
      series: $("xml_path").textContent,
      powers: $("powers").value,
      seed: $("seed").value,
      repetitions: $("repetitions").value,
      stim_dff_min: $("stim_dff_min").value,
      via_relay: $("via_relay").value,
      serial: $("serial").value,
      stim_mode: $("stim_mode").value,
      trigger: $("trigger").value,
    });
    if (!res.ok) $("save_status").textContent = res.error || "run failed";
  });
  $("stop").addEventListener("click", () => jpost("/api/stop", {}));
  $("sel_top").addEventListener("click", () => {
    picked.clear();
    lastRanked.filter((r) => r.tercile === "top").forEach((r) => picked.add(String(r.id)));
    renderCandidates(lastRanked);
  });
  $("sel_clear").addEventListener("click", () => {
    picked.clear();
    renderCandidates(lastRanked);
  });
  $("save_xml").addEventListener("click", async () => {
    const res = await jpost("/api/save-candidates", { ids: [...picked] });
    $("save_status").textContent = res.ok ? `saved ${res.path}` : res.error || "save failed";
  });
  fitGrid();
  window.addEventListener("resize", fitGrid);
  setInterval(refreshRelay, 4000);
  setInterval(refreshLive, 150);
  setInterval(refreshPoints, 2000);
  setInterval(tick, 800);
}

boot();
