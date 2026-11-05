const $ = (id) => document.getElementById(id);
// This branch: offline random groups + manual PV run (no GUI Run/DTR).
const RUN_STOP_DISABLED = true;
const picked = new Set();
let lastRanked = [];
const selectedGroups = new Set();
let lastGroups = [];

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
let liveMode = "raw";
let baselineReady = false;

function applyMarksVisibility() {
  const svg = $("live_marks");
  svg.classList.toggle("hidden", !marksOn);
  // Inline display so CSS specificity cannot keep marks stuck visible.
  svg.style.display = marksOn ? "block" : "none";
  // Keep the note's height when hiding marks so the FOV does not jump.
  const note = $("marks_note");
  note.textContent = marksNote;
  note.classList.toggle("dimmed", !marksOn);
  const btn = $("marks_toggle");
  btn.setAttribute("aria-pressed", marksOn ? "true" : "false");
  btn.title = marksOn ? "Hide Mark Points overlay" : "Show Mark Points overlay";
}

function applyDffToggle() {
  const btn = $("dff_toggle");
  const dff = liveMode === "dff";
  btn.textContent = dff ? "ΔF/F" : "Raw";
  btn.setAttribute("aria-pressed", dff ? "true" : "false");
  btn.disabled = !baselineReady && !dff;
  btn.title = dff
    ? "Showing %ΔF/F — click for raw FOV"
    : baselineReady
      ? "Showing raw FOV — click for %ΔF/F"
      : "Collect a baseline first";
}

function fmtDffPct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  const s = n >= 0 ? `+${n.toFixed(0)}` : n.toFixed(0);
  return `${s}%`;
}

function rgbCss(rgb) {
  const [r, g, b] = rgb || [0, 255, 255];
  return `rgb(${r},${g},${b})`;
}

function toggleGroupSelection(id) {
  const gid = String(id);
  if (selectedGroups.has(gid)) selectedGroups.delete(gid);
  else selectedGroups.add(gid);
  renderSelectedGroups(lastGroups);
  // Persist to server so /api/points highlights stay in sync.
  jpost("/api/select-groups", { ids: [...selectedGroups] }).then(() => refreshPoints());
}

function renderSelectedGroups(groups) {
  lastGroups = groups || [];
  const box = $("group_box");
  const tb = $("group_table").querySelector("tbody");
  tb.innerHTML = "";
  if (!lastGroups.length) {
    box.hidden = true;
    $("side").classList.remove("has-groups");
    return;
  }
  box.hidden = false;
  $("side").classList.add("has-groups");
  for (const g of lastGroups) {
    const tr = document.createElement("tr");
    const id = String(g.id);
    if (selectedGroups.has(id)) tr.classList.add("picked");
    const swatch = `style="color:${rgbCss(g.rgb)}"`;
    tr.innerHTML =
      `<td>${selectedGroups.has(id) ? "●" : "○"}</td>` +
      `<td ${swatch}>${g.name || "G" + id}</td>` +
      `<td>${(g.point_ids || []).join(", ")}</td>`;
    tr.addEventListener("click", () => toggleGroupSelection(id));
    tb.appendChild(tr);
  }
}

function toggleGroupsForPoint(pointId) {
  const gids = (lastGroups || [])
    .filter((g) => (g.point_ids || []).map(String).includes(String(pointId)))
    .map((g) => String(g.id));
  if (!gids.length) return;
  const active = gids.find((id) => selectedGroups.has(id));
  toggleGroupSelection(active || gids[0]);
}

/** Map SVG onto the same rectangle object-fit:contain uses for #live_img. */
function syncMarksToImage() {
  const img = $("live_img");
  const svg = $("live_marks");
  const wrap = img && img.parentElement;
  if (!img || !svg || !wrap) return;
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  const cw = wrap.clientWidth || 0;
  const ch = wrap.clientHeight || 0;
  if (nw < 1 || nh < 1 || cw < 1 || ch < 1) {
    svg.style.left = "0";
    svg.style.top = "0";
    svg.style.width = "100%";
    svg.style.height = "100%";
    return;
  }
  const scale = Math.min(cw / nw, ch / nh);
  const dw = nw * scale;
  const dh = nh * scale;
  svg.style.left = `${(cw - dw) / 2}px`;
  svg.style.top = `${(ch - dh) / 2}px`;
  svg.style.width = `${dw}px`;
  svg.style.height = `${dh}px`;
}

async function refreshPoints() {
  let res;
  try {
    res = await jget("/api/points");
  } catch (_) {
    return;
  }
  const svg = $("live_marks");
  // Unit square viewBox: fx/fy are already 0–1 of the live frame.
  // SVG element is sized to the contained image box (not the letterbox bars).
  svg.setAttribute("viewBox", "0 0 1 1");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.replaceChildren();
  syncMarksToImage();
  const hasGroups = (res.groups || []).length > 0;
  for (const p of res.points || []) {
    const cx = Number(p.fx);
    const cy = Number(p.fy);
    if (!(cx >= 0 && cx <= 1 && cy >= 0 && cy <= 1)) {
      // Still draw slightly OOB so galvo edge points stay visible.
    }
    const inGroup = (p.group_ids || []).length > 0;
    // Small ring at the point — spiral size in the XML is not image radius.
    const rad = 0.012;
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute(
      "class",
      "mp-pt" + (p.group_selected ? " selected" : "") + (inGroup ? " grouped" : "")
    );
    c.setAttribute("cx", cx);
    c.setAttribute("cy", cy);
    c.setAttribute("r", rad);
    c.setAttribute("stroke-width", p.group_selected ? 0.006 : 0.003);
    if (p.rgb) c.setAttribute("stroke", rgbCss(p.rgb));
    if (inGroup) {
      c.style.cursor = "pointer";
      c.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggleGroupsForPoint(p.id);
      });
    }
    const t = document.createElementNS(SVG_NS, "text");
    t.setAttribute("class", "mp-pt" + (inGroup ? " grouped" : ""));
    t.setAttribute("x", cx + 0.016);
    t.setAttribute("y", cy - 0.012);
    t.setAttribute("font-size", "0.028");
    t.setAttribute("stroke-width", "0.004");
    if (p.rgb) t.setAttribute("fill", rgbCss(p.rgb));
    const dffBit = p.dff_pct != null ? ` ${fmtDffPct(p.dff_pct)}` : "";
    t.textContent = `${p.id}${dffBit}`;
    if (inGroup) {
      t.style.cursor = "pointer";
      t.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggleGroupsForPoint(p.id);
      });
    }
    svg.append(c, t);
  }
  svg.classList.toggle("pickable", hasGroups);
  marksNote = res.note || "";
  applyMarksVisibility();
}

async function saveTextClient(text, filename, description) {
  const name = filename || "groups.xml";
  const isGpl = /\.gpl$/i.test(name);
  const accept = isGpl
    ? { "application/xml": [".gpl"] }
    : { "application/xml": [".xml"] };
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: name,
        types: [{ description: description || "Mark Points file", accept }],
      });
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      return { ok: true, via: "picker", name };
    } catch (e) {
      if (e && e.name === "AbortError") return { ok: false, cancelled: true };
      // fall through to download
    }
  }
  const blob = new Blob([text], { type: "application/xml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
  return { ok: true, via: "download", name };
}

async function saveSeriesAndGpl(res) {
  const xmlSaved = await saveTextClient(
    res.xml,
    res.filename || "random_groups.xml",
    "Mark Points series XML"
  );
  if (xmlSaved.cancelled) return xmlSaved;
  if (res.gpl) {
    const gplSaved = await saveTextClient(
      res.gpl,
      res.gpl_filename || "random_groups.gpl",
      "Galvo Points list (.gpl)"
    );
    if (gplSaved.cancelled) {
      return { ok: true, name: xmlSaved.name, gpl: null, gplSkipped: true };
    }
    return { ok: true, name: xmlSaved.name, gpl: gplSaved.name };
  }
  return { ok: true, name: xmlSaved.name, gpl: null };
}

async function refreshLive() {
  try {
    const r = await fetch("/api/live.png?t=" + Date.now());
    if (!r.ok) return;
    const blob = await r.blob();
    const img = $("live_img");
    const old = img.src;
    img.onload = () => syncMarksToImage();
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
  });
  if (!res.ok) {
    $("xml_path").textContent = res.error || "parse failed";
    return;
  }
  $("xml_path").textContent = res.path;
  showXmlFields(res.fields || {});
  refreshPoints();
  if (res.fields && res.fields.repetitions) $("gen_reps").value = res.fields.repetitions;
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
    return;
  }
  box.hidden = false;
  $("side").classList.add("has-cands");
  if (!picked.size) {
    lastRanked.filter((r) => r.stimulated).forEach((r) => picked.add(rowKey(r)));
  }
  for (const r of lastRanked) {
    const tr = document.createElement("tr");
    const key = rowKey(r);
    const pwr = r.power ?? r.min_power;
    if (picked.has(key)) tr.classList.add("picked");
    tr.innerHTML = `<td>${picked.has(key) ? "●" : "○"}</td><td>${r.id}</td><td>${pwr ?? "—"}</td><td>${r.dff == null ? "—" : Number(r.dff).toFixed(3)}</td><td>${r.tercile}</td>`;
    tr.addEventListener("click", () => {
      if (picked.has(key)) picked.delete(key);
      else picked.add(key);
      renderCandidates(lastRanked);
    });
    tb.appendChild(tr);
  }
}

async function tick() {
  const st = await jget("/api/status");
  $("log").textContent = (st.log || []).join("\n");
  $("log").scrollTop = $("log").scrollHeight;
  if (RUN_STOP_DISABLED) {
    $("run").disabled = true;
    $("stop").disabled = true;
  } else {
    $("run").disabled = !!st.running;
    $("stop").disabled = !st.running;
  }
  if (st.has_candidates) renderCandidates(st.ranked);
  if (st.random_groups && st.random_groups.length) {
    if (!lastGroups.length && (st.selected_group_ids || []).length) {
      selectedGroups.clear();
      (st.selected_group_ids || []).forEach((id) => selectedGroups.add(String(id)));
    }
    renderSelectedGroups(st.random_groups);
  }
  if (st.error) $("save_status").textContent = st.error;
  if (st.live) {
    liveMode = st.live.mode || "raw";
    baselineReady = !!st.live.baseline_ready;
    applyDffToggle();
    const o = st.live.optics || {};
    if (o.fov_width_um && o.optical_zoom) {
      $("live_cap").textContent =
        (st.live.detail || "live") +
        (!String(st.live.detail || "").includes("FOV")
          ? ` · FOV ${Number(o.fov_width_um).toFixed(1)} µm · zoom ${o.optical_zoom}`
          : "");
    } else {
      $("live_cap").textContent = st.live.detail || "";
    }
  }
  if (st.boards && st.boards.length) {
    ensurePowerTiles(st.running ? st.boards : st.boards.filter((b) => b.ready));
  }
}

async function boot() {
  const d = await jget("/api/defaults");
  $("via_relay").value = d.via_relay;
  if (!$("gen_power").value) $("gen_power").value = "160";
  if (!$("gen_reps").value) $("gen_reps").value = d.repetitions || 1;
  fillSelect($("serial"), d.com_ports, "device", (p) => `${p.device} — ${p.description}`, d.serial);
  fillSelect($("stim_mode"), d.stim_modes, null, null, d.stim_mode);
  fillSelect($("trigger"), d.triggers, null, null, d.trigger);
  setLight(d.relay || {});
  $("via_relay").addEventListener("change", refreshRelay);
  $("xml_file").addEventListener("change", onXmlFile);
  $("baseline_btn").addEventListener("click", async () => {
    const res = await jpost("/api/live-baseline", {});
    if (!res.ok) {
      $("live_cap").textContent = res.error || "baseline failed";
      return;
    }
    liveMode = res.mode || "raw";
    baselineReady = !!res.baseline_ready;
    applyDffToggle();
    $("live_cap").textContent = res.detail || "collecting baseline…";
  });
  $("dff_toggle").addEventListener("click", async () => {
    if (!baselineReady && liveMode !== "dff") return;
    const next = liveMode === "dff" ? "raw" : "dff";
    const res = await jpost("/api/live-mode", { mode: next });
    if (!res.ok) {
      $("live_cap").textContent = res.error || "mode switch failed";
      return;
    }
    liveMode = res.mode || next;
    baselineReady = !!res.baseline_ready;
    applyDffToggle();
  });
  $("marks_toggle").addEventListener("click", () => {
    marksOn = !marksOn;
    applyMarksVisibility();
  });
  applyMarksVisibility();
  $("run").addEventListener("click", async () => {
    if (RUN_STOP_DISABLED) return;
    const res = await jpost("/api/run", {
      series: $("xml_path").textContent,
      powers: $("gen_power").value,
      seed: $("gen_seed").value,
      repetitions: $("gen_reps").value,
      via_relay: $("via_relay").value,
      serial: $("serial").value,
      stim_mode: $("stim_mode").value,
      trigger: $("trigger").value,
    });
    if (!res.ok) $("save_status").textContent = res.error || "run failed";
  });
  $("stop").addEventListener("click", () => {
    if (RUN_STOP_DISABLED) return;
    jpost("/api/stop", {});
  });
  $("sel_top").addEventListener("click", () => {
    picked.clear();
    lastRanked.filter((r) => r.tercile === "top").forEach((r) => picked.add(rowKey(r)));
    renderCandidates(lastRanked);
  });
  $("sel_clear").addEventListener("click", () => {
    picked.clear();
    renderCandidates(lastRanked);
  });
  $("save_xml").addEventListener("click", async () => {
    const res = await jpost("/api/save-candidates", { ids: [...picked], keys: [...picked] });
    $("save_status").textContent = res.ok ? `saved ${res.path}` : res.error || "save failed";
  });
  $("gen_groups").addEventListener("click", async () => {
    $("gen_status").textContent = "";
    $("seq_status").textContent = "";
    const series = $("xml_path").textContent;
    let kind = "points_pool";
    try {
      const info = await jpost("/api/series-kind", { series });
      if (!info.ok) {
        $("gen_status").textContent = info.error || "cannot classify series";
        $("gen_dlg").showModal();
        return;
      }
      kind = info.kind || "points_pool";
      if (kind === "group_catalog") {
        $("seq_catalog").textContent =
          `catalog: ${info.n_groups} groups · ${info.n_points} points`;
        $("seq_dlg").showModal();
        return;
      }
      const n = Number(info.n_points) || 0;
      if (n > 0) {
        const need = Math.ceil(n / 3);
        const cur = Number($("gen_n").value) || 0;
        if (cur < need) $("gen_n").value = need;
      }
    } catch (_) {
      try {
        const pts = await jget("/api/points");
        const n = (pts.points || []).length;
        if (n > 0) {
          const need = Math.ceil(n / 3);
          const cur = Number($("gen_n").value) || 0;
          if (cur < need) $("gen_n").value = need;
        }
      } catch (_) {}
    }
    $("gen_dlg").showModal();
  });
  $("gen_cancel").addEventListener("click", () => $("gen_dlg").close());
  $("seq_cancel").addEventListener("click", () => $("seq_dlg").close());
  $("gen_form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("gen_status").textContent = "building…";
    const body = {
      series: $("xml_path").textContent,
      mode: "points_pool",
      n_groups: $("gen_n").value,
      power: $("gen_power").value,
      repetitions: $("gen_reps").value,
      seed: $("gen_seed").value,
    };
    const res = await jpost("/api/random-groups", body);
    if (!res.ok) {
      $("gen_status").textContent = res.error || "generate failed";
      return;
    }
    selectedGroups.clear();
    renderSelectedGroups(res.groups || []);
    refreshPoints();
    const saved = await saveSeriesAndGpl(res);
    const cover =
      res.n_covered != null && res.n_points != null
        ? ` · covered ${res.n_covered}/${res.n_points} points`
        : "";
    if (saved.cancelled) {
      $("gen_status").textContent =
        `built ${res.n_groups} groups${cover} (server ${res.path}` +
        (res.gpl_path ? ` + ${res.gpl_path}` : "") +
        `)`;
    } else {
      const gplBit = saved.gpl ? ` + ${saved.gpl}` : saved.gplSkipped ? " (gpl skipped)" : "";
      $("gen_status").textContent =
        `saved ${saved.name}${gplBit} · ${res.n_groups} groups${cover}`;
      $("grp_status").textContent =
        `generated ${res.n_groups} groups @ power ${res.power}${cover} — load .gpl then .xml in PV`;
    }
    $("gen_dlg").close();
  });
  $("seq_form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("seq_status").textContent = "building…";
    const body = {
      series: $("xml_path").textContent,
      mode: "sequence",
      n_sequences: $("seq_n").value,
      seed: $("seq_seed").value,
    };
    const res = await jpost("/api/random-groups", body);
    if (!res.ok) {
      $("seq_status").textContent = res.error || "sequence failed";
      return;
    }
    selectedGroups.clear();
    renderSelectedGroups(res.groups || []);
    refreshPoints();
    const saved = await saveSeriesAndGpl(res);
    const n = res.n_sequences || res.n_groups;
    if (saved.cancelled) {
      $("seq_status").textContent =
        `built sequence n=${n} (server ${res.path}` +
        (res.gpl_path ? ` + ${res.gpl_path}` : "") +
        `)`;
    } else {
      const gplBit = saved.gpl ? ` + ${saved.gpl}` : saved.gplSkipped ? " (gpl skipped)" : "";
      $("seq_status").textContent = `saved ${saved.name}${gplBit} · sequence n=${n}`;
      $("grp_status").textContent =
        `group sequence n=${n} (catalog ${res.n_catalog}) — load .gpl then .xml in PV`;
    }
    $("seq_dlg").close();
  });
  $("grp_clear").addEventListener("click", () => {
    selectedGroups.clear();
    jpost("/api/select-groups", { ids: [] }).then(() => {
      renderSelectedGroups(lastGroups);
      refreshPoints();
    });
  });
  $("grp_save").addEventListener("click", async () => {
    if (!selectedGroups.size) {
      $("grp_status").textContent = "select at least one group";
      return;
    }
    const res = await jpost("/api/selected-groups-xml", { ids: [...selectedGroups] });
    if (!res.ok) {
      $("grp_status").textContent = res.error || "save failed";
      return;
    }
    const saved = await saveSeriesAndGpl(res);
    if (saved.cancelled) {
      $("grp_status").textContent =
        `server ${res.path}` + (res.gpl_path ? ` + ${res.gpl_path}` : "");
    } else {
      const gplBit = saved.gpl ? ` + ${saved.gpl}` : "";
      $("grp_status").textContent =
        `saved ${saved.name}${gplBit} (${res.n_groups} groups) — load .gpl then .xml in PV`;
    }
  });
  fitGrid();
  syncMarksToImage();
  window.addEventListener("resize", () => {
    fitGrid();
    syncMarksToImage();
  });
  setInterval(refreshRelay, 4000);
  setInterval(refreshLive, 150);
  setInterval(refreshPoints, 2000);
  setInterval(tick, 800);
}

boot();
