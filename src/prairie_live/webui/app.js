const $ = (id) => document.getElementById(id);
const picked = new Set();
let lastRanked = [];
/** When ranked results change, seed ● on stimulated rows once. Clear selection must stay empty. */
let rankedSig = "";
/** After Clear selection: do not auto-reselect (keeps green row highlight off). */
let selectionCleared = false;

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
      // Match Live scan structure (head / fov-wrap / caps) so FOVs align horizontally.
      tile.innerHTML =
        `<div class="tile-head"><h3></h3></div>` +
        `<div class="fov-wrap"><img alt="power ${key}" /></div>` +
        `<div class="tile-caps"><p class="muted"></p></div>`;
      grid.appendChild(tile);
      fitGrid();
    }
    const tile = grid.querySelector(`[data-power="${key}"]`);
    tile.querySelector("h3").textContent = `power ${key}`;
    const cap = tile.querySelector(".tile-caps p");
    if (b.ready) {
      const img = tile.querySelector("img");
      img.src = `/api/board.png?power=${encodeURIComponent(key)}&t=${Date.now()}`;
      const rest = String(b.title || "").replace(/^power\s+\S+\s*·?\s*/, "");
      cap.textContent = rest || "updated";
    } else {
      cap.textContent = "waiting for first trial…";
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

function rowKey(r) {
  return String(r.key || `${r.id}@${r.power ?? r.min_power ?? ""}`);
}

function renderCandidates(ranked) {
  lastRanked = ranked || [];
  const box = $("cand_box");
  const tb = $("cand_table").querySelector("tbody");
  tb.innerHTML = "";
  if (!lastRanked.length) {
    box.hidden = true;
    $("side").classList.remove("has-cands");
    rankedSig = "";
    selectionCleared = false;
    return;
  }
  box.hidden = false;
  $("side").classList.add("has-cands");
  // Sorted so poll order cannot look like a new run and re-seed selection/green.
  const sig = lastRanked.map(rowKey).slice().sort().join("\0");
  if (sig !== rankedSig) {
    // New run results only — do not re-select after the user hits Clear selection.
    rankedSig = sig;
    selectionCleared = false;
    picked.clear();
    lastRanked.filter((r) => r.stimulated).forEach((r) => picked.add(rowKey(r)));
  } else if (selectionCleared) {
    picked.clear();
  }
  for (const r of lastRanked) {
    const tr = document.createElement("tr");
    const key = rowKey(r);
    const pwr = r.power ?? r.min_power;
    if (picked.has(key)) tr.classList.add("picked");
    tr.innerHTML = `<td>${picked.has(key) ? "●" : "○"}</td><td>${r.id}</td><td>${pwr ?? "—"}</td><td>${r.dff == null ? "—" : Number(r.dff).toFixed(3)}</td><td>${r.tercile}</td>`;
    tr.addEventListener("click", () => {
      selectionCleared = false;
      if (picked.has(key)) picked.delete(key);
      else picked.add(key);
      renderCandidates(lastRanked);
    });
    tb.appendChild(tr);
  }
}

let lastLogText = "";

function renderLog(lines) {
  const el = $("log");
  const text = (lines || []).join("\n");
  // Always refresh — pause-on-select froze the Trial log blank for whole Runs.
  if (text === lastLogText) return;
  const pinBottom =
    el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.textContent = text;
  lastLogText = text;
  if (pinBottom) el.scrollTop = el.scrollHeight;
}

async function tick() {
  try {
    const st = await jget("/api/status");
    renderLog(st.log || []);
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
  } catch (e) {
    console.warn("status poll failed", e);
  }
}

function syncVisualOpts() {
  const on = $("visual_enabled").checked;
  $("visual_opts").hidden = !on;
}

function visualRunPayload() {
  return {
    visual_enabled: $("visual_enabled").checked,
    visual_screen: $("visual_screen").value,
    visual_refresh_hz: $("visual_refresh_hz").value,
    visual_isi_s: $("visual_isi_s").value,
    visual_lead_ms: $("visual_lead_ms").value,
    visual_grating_frames: $("visual_grating_frames").value,
    visual_dtr_frames: $("visual_dtr_frames").value,
  };
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
  $("visual_enabled").checked = !!d.visual_enabled;
  $("visual_screen").value = d.visual_screen;
  $("visual_refresh_hz").value = d.visual_refresh_hz;
  $("visual_isi_s").value = d.visual_isi_s;
  $("visual_lead_ms").value = d.visual_lead_ms;
  $("visual_grating_frames").value = d.visual_grating_frames;
  $("visual_dtr_frames").value = d.visual_dtr_frames;
  syncVisualOpts();
  setLight(d.relay || {});
  $("via_relay").addEventListener("change", refreshRelay);
  $("powers").addEventListener("input", syncTypedPowers);
  $("xml_file").addEventListener("change", onXmlFile);
  $("visual_enabled").addEventListener("change", syncVisualOpts);
  $("marks_toggle").addEventListener("click", () => {
    marksOn = !marksOn;
    applyMarksVisibility();
  });
  applyMarksVisibility();
  $("log_copy").addEventListener("click", async () => {
    const text = $("log").textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      $("log_copy").textContent = "Copied";
      setTimeout(() => {
        $("log_copy").textContent = "Copy";
      }, 1200);
    } catch (_) {
      // Fallback when clipboard API is blocked.
      const r = document.createRange();
      r.selectNodeContents($("log"));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      document.execCommand("copy");
      sel.removeAllRanges();
      $("log_copy").textContent = "Copied";
      setTimeout(() => {
        $("log_copy").textContent = "Copy";
      }, 1200);
    }
  });
  $("run").addEventListener("click", async () => {
    lastLogText = "";
    $("log").textContent = "starting…";
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
      ...visualRunPayload(),
    });
    if (!res.ok) {
      const err = res.error || "run failed";
      $("save_status").textContent = err;
      // "starting…" is client-only — replace it so a 409 does not look hung.
      lastLogText = "";
      $("log").textContent =
        err.includes("already")
          ? "Run rejected — already running. Hit Stop, then Run again."
          : `Run failed: ${err}`;
      return;
    }
    // Pull server log immediately (do not wait for the 800ms poll).
    lastLogText = "";
    await tick();
  });
  $("stop").addEventListener("click", async () => {
    $("save_status").textContent = "stopping…";
    await jpost("/api/stop", {});
    lastLogText = "";
    await tick();
  });
  $("sel_top").addEventListener("click", () => {
    selectionCleared = false;
    picked.clear();
    lastRanked.filter((r) => r.tercile === "top").forEach((r) => picked.add(rowKey(r)));
    renderCandidates(lastRanked);
  });
  $("sel_clear").addEventListener("click", () => {
    // Clear ● + green highlight; list/ranking stays. Poll must not re-seed.
    selectionCleared = true;
    picked.clear();
    renderCandidates(lastRanked);
  });
  $("save_xml").addEventListener("click", async () => {
    const res = await jpost("/api/save-candidates", { ids: [...picked], keys: [...picked] });
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
