const canvas = document.getElementById("arena");
const ctx = canvas.getContext("2d");
const hud = document.getElementById("hud");
const badge = document.getElementById("trial-badge");
const proto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${proto}://${location.host}/ws`);

let state = null;
let pointerMm = null;

ws.onmessage = (ev) => {
	state = JSON.parse(ev.data);
	draw();
	renderHud();
};

function sendJson(obj) {
	if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

canvas.addEventListener("mousemove", (e) => {
	if (!state) return;
	const r = canvas.getBoundingClientRect();
	const nx = (e.clientX - r.left) / r.width;
	const ny = (e.clientY - r.top) / r.height;
	pointerMm = {
		x_mm: nx * state.arena.width_mm,
		y_mm: ny * state.arena.height_mm,
	};
	sendJson({ type: "pointer", ...pointerMm });
});

document.querySelectorAll("[data-trial]").forEach((btn) => {
	btn.addEventListener("click", () => sendJson({ type: "trial", cmd: btn.dataset.trial }));
});

window.addEventListener("keydown", (e) => {
	const k = e.key.toLowerCase();
	if (k === "s") sendJson({ type: "trial", cmd: "start" });
	if (k === "e") sendJson({ type: "trial", cmd: "end" });
	if (k === "r") sendJson({ type: "trial", cmd: "reset" });
});

function mmToPx(x, y) {
	const a = state.arena;
	return [(x / a.width_mm) * canvas.width, (y / a.height_mm) * canvas.height];
}

function draw() {
	if (!state) return;
	const w = canvas.width;
	const h = canvas.height;
	ctx.fillStyle = "#0d0f0c";
	ctx.fillRect(0, 0, w, h);
	drawGrid();
	drawThreatRings();
	drawCone();
	drawFlee();
	drawGhost();
	drawPrey();
	drawFerret();
}

function drawGrid() {
	ctx.strokeStyle = "#22261e";
	ctx.lineWidth = 1;
	for (let i = 0; i <= 8; i++) {
		const x = (i / 8) * canvas.width;
		const y = (i / 8) * canvas.height;
		ctx.beginPath();
		ctx.moveTo(x, 0);
		ctx.lineTo(x, canvas.height);
		ctx.stroke();
		ctx.beginPath();
		ctx.moveTo(0, y);
		ctx.lineTo(canvas.width, y);
		ctx.stroke();
	}
}

function drawThreatRings() {
	const f = state.ferret_camera;
	if (!f.valid) return;
	const [cx, cy] = mmToPx(f.x_mm, f.y_mm);
	const gsd = canvas.width / state.arena.width_mm;
	const pref = state.policy.preferred_gap_mm || state.policy.threat_distance_mm;
	const minG = state.policy.min_gap_mm || pref * 0.4;
	ctx.strokeStyle = "rgba(226,184,74,0.35)";
	circle(cx, cy, pref * gsd);
	ctx.strokeStyle = "rgba(211,107,94,0.3)";
	circle(cx, cy, minG * gsd);
	// Wall keep-out band (arena edge margin).
	const m = (state.policy.wall_margin_mm || 280) * gsd;
	ctx.strokeStyle = "rgba(126,200,196,0.2)";
	ctx.strokeRect(m, m, canvas.width - 2 * m, canvas.height - 2 * m);
}

function drawCone() {
	// Soft keep-away no longer uses cone-of-impact flees.
}

function drawFlee() {
	// No discrete flee target marker.
}

function drawGhost() {
	const f = state.ferret_camera;
	if (!f.valid) return;
	const [x, y] = mmToPx(f.x_mm, f.y_mm);
	ctx.fillStyle = "#8a7a4a";
	ctx.globalAlpha = 0.55;
	blob(x, y, 10);
	ctx.globalAlpha = 1;
}

function drawFerret() {
	const f = state.ferret_true;
	const [x, y] = mmToPx(f.x_mm, f.y_mm);
	ctx.fillStyle = "#e2b84a";
	blob(x, y, 8);
	heading(x, y, f.direction_deg, "#e2b84a");
}

function drawPrey() {
	const p = state.prey;
	const [x, y] = mmToPx(p.x_mm, p.y_mm);
	ctx.fillStyle = "#7ec8c4";
	ctx.beginPath();
	if (ctx.roundRect) {
		ctx.roundRect(x - 8, y - 5, 16, 10, 3);
	} else {
		ctx.rect(x - 8, y - 5, 16, 10);
	}
	ctx.fill();
	heading(x, y, p.direction_deg, "#7ec8c4");
}

function blob(x, y, r) {
	ctx.beginPath();
	ctx.arc(x, y, r, 0, Math.PI * 2);
	ctx.fill();
}

function circle(x, y, r) {
	ctx.beginPath();
	ctx.arc(x, y, r, 0, Math.PI * 2);
	ctx.stroke();
}

function heading(x, y, deg, color) {
	const rad = (-deg * Math.PI) / 180;
	ctx.strokeStyle = color;
	ctx.beginPath();
	ctx.moveTo(x, y);
	ctx.lineTo(x + Math.cos(rad) * 22, y + Math.sin(rad) * 22);
	ctx.stroke();
}

function renderHud() {
	const s = state;
	badge.textContent = s.trial;
	badge.className = "badge " + s.trial;
	const c = s.camera;
	const z = s.zaber;
	const d = s.decision;
	const sc = s.scene;
	hud.innerHTML = `
		<h2>Basler / pylon</h2>
		${row("model", c.model)}
		${row("format", `${c.pixel_format} ${s.arena.width_px}×${s.arena.height_px}`)}
		${row("fps cap", c.fps.toFixed(0))}
		${row("exposure", c.exposure_us.toFixed(0) + " µs")}
		${row("USB transfer", c.usb_transfer_ms.toFixed(2) + " ms")}
		${row("MOG2/track", c.tracking_pipeline_ms.toFixed(2) + " ms")}
		${row("grab→host", c.grab_to_host_ms.toFixed(2) + " ms")}
		${row("grab→frame", c.last_grab_to_frame_ms.toFixed(2) + " ms")}
		${row("strategy", c.strategy)}
		${row("delivered / dropped", `${c.delivered} / ${c.dropped}`)}
		${row("GSD", s.arena.gsd_mm_per_px + " mm/px")}
		${row("FOV", `${s.arena.width_mm.toFixed(0)} × ${s.arena.height_mm.toFixed(0)} mm`)}

		<h2>Zaber API</h2>
		${row("link", z.comm + " RTT " + z.rtt_ms.toFixed(1) + " ms")}
		${row("busy", String(z.busy), z.busy ? "warn" : "ok")}
		${row("position", `${z.x_mm.toFixed(1)}, ${z.y_mm.toFixed(1)} mm`)}
		${row("velocity", `${z.speed_mm_s.toFixed(0)} mm/s  ${z.heading_deg.toFixed(0)}°`)}
		${row("limits", `${z.max_speed_mm_s} mm/s · ${z.max_accel_mm_s2} mm/s²`)}
		<ul class="calls">${z.api_calls.map((a) => `<li>${a.name} ${esc(a.detail)}</li>`).join("")}</ul>

		<h2>Ferret (pointer / camera)</h2>
		${row("true", fmtTrack(s.ferret_true))}
		${row("camera seen", fmtTrack(s.ferret_camera))}

		<h2>Prey (gantry encoder)</h2>
		${row("state", fmtTrack(s.prey))}
		${row("gap", sc.distance_mm.toFixed(0) + " mm")}
		${row("bearing", sc.bearing_deg.toFixed(0) + "°")}
		${row("closing", sc.closing_speed_mm_s.toFixed(0) + " mm/s")}

		<h2>Chase decision @ ${s.control_hz.toFixed(0)} Hz</h2>
		<div class="reason">${esc(d.reason)}</div>
		${row("preferred gap", (s.policy.preferred_gap_mm || 0).toFixed(0) + " mm")}
		${row("gap error", (d.gap_error_mm || 0).toFixed(0) + " mm (+: too close)")}
		${row("wall push", (d.wall_push || 0).toFixed(2))}
		${row("cmd v", `${d.vx_mm_s.toFixed(0)}, ${d.vy_mm_s.toFixed(0)} mm/s`)}
		${row("cap", (s.policy.max_engage_speed_mm_s || 0).toFixed(0) + " mm/s")}
		${row("policy compute", d.compute_ms.toFixed(3) + " ms")}
		${row("stale stops", String(d.stale_stops), d.stale_stops ? "warn" : "")}
	`;
}

function row(k, v, cls) {
	return `<div class="row"><span class="k">${k}</span><span class="v ${cls || ""}">${v}</span></div>`;
}

function fmtTrack(t) {
	return `${t.x_mm.toFixed(0)}, ${t.y_mm.toFixed(0)} mm · ${t.speed_mm_s.toFixed(0)} mm/s · ${t.direction_deg.toFixed(0)}°`;
}

function esc(s) {
	return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
