"""Desktop launch form: Fat XML + powers → trial boards → candidate picker."""

from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from prairie_live.experiment_config import (
	default_config_path,
	load_experiment,
	powers_to_cli_default,
)
from prairie_live.gui_session import (
	GROUP_SIZE,
	apply_gui_overrides,
	n_groups_for_pool,
	parse_powers_field,
	planned_trial_count,
	trial_log_line,
)
from prairie_live.markpoints import extract_unique_points, load_mark_points_file
from prairie_live.power_view import PowerBoards


def main(argv: list[str] | None = None) -> None:
	p = argparse.ArgumentParser(description="prairie-live mapping GUI")
	p.add_argument("--config", "-c", default=None, help="experiment.json")
	args = p.parse_args(argv)
	cfg_path = args.config
	if cfg_path is None and default_config_path().is_file():
		cfg_path = str(default_config_path())
	cfg = load_experiment(cfg_path) if cfg_path else {}
	if cfg_path:
		print(f"config: {cfg_path}")
	App(cfg).run()


class App:
	def __init__(self, cfg: dict) -> None:
		self.cfg = dict(cfg)
		self.root = tk.Tk()
		self.root.title("prairie-live — mapping")
		self.root.geometry("720x520")
		self.q: queue.Queue = queue.Queue()
		self._stop = threading.Event()
		self._worker: threading.Thread | None = None
		self.boards: PowerBoards | None = None
		self.points: list[dict] = []
		self._build()
		self.root.after(80, self._poll)

	def run(self) -> None:
		self.root.mainloop()

	def _build(self) -> None:
		frm = ttk.Frame(self.root, padding=10)
		frm.pack(fill=tk.BOTH, expand=True)

		self.series_var = tk.StringVar(value=str(self.cfg.get("series") or ""))
		self.powers_var = tk.StringVar(
			value=powers_to_cli_default(self.cfg.get("powers") or [140])
		)
		self.seed_var = tk.StringVar(value=str(self.cfg.get("seed") if self.cfg.get("seed") is not None else 0))
		self.reps_var = tk.StringVar(value=str(self.cfg.get("repetitions") or 1))
		self.thresh_var = tk.StringVar(value=str(self.cfg.get("stim_dff_min") if self.cfg.get("stim_dff_min") is not None else 0.0))
		self.ngroups_var = tk.StringVar(value="")
		self.plan_var = tk.StringVar(value="")

		row = 0
		ttk.Label(frm, text="Fat Mark Points XML").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.series_var, width=56).grid(row=row, column=1, sticky=tk.EW)
		ttk.Button(frm, text="Browse…", command=self._browse).grid(row=row, column=2, padx=4)

		row = 1
		ttk.Label(frm, text="Powers").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.powers_var, width=56).grid(
			row=row, column=1, columnspan=2, sticky=tk.EW
		)

		row = 2
		ttk.Label(frm, text="Seed").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.seed_var, width=12).grid(row=row, column=1, sticky=tk.W)

		row = 3
		ttk.Label(frm, text="Repetitions").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.reps_var, width=12).grid(row=row, column=1, sticky=tk.W)

		row = 4
		ttk.Label(frm, text="Stim ΔF/F min").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.thresh_var, width=12).grid(row=row, column=1, sticky=tk.W)

		row = 5
		ttk.Label(frm, text="N groups (blank = use all points)").grid(row=row, column=0, sticky=tk.W)
		ttk.Entry(frm, textvariable=self.ngroups_var, width=12).grid(row=row, column=1, sticky=tk.W)

		row = 6
		ttk.Label(frm, textvariable=self.plan_var).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(4, 6))

		row = 7
		btns = ttk.Frame(frm)
		btns.grid(row=row, column=0, columnspan=3, sticky=tk.W)
		self.run_btn = ttk.Button(btns, text="Run", command=self._on_run)
		self.run_btn.pack(side=tk.LEFT, padx=(0, 8))
		self.stop_btn = ttk.Button(btns, text="Stop", command=self._on_stop, state=tk.DISABLED)
		self.stop_btn.pack(side=tk.LEFT)

		row = 8
		ttk.Label(frm, text="Trial log").grid(row=row, column=0, sticky=tk.W, pady=(10, 0))
		row = 9
		self.log = tk.Text(frm, height=16, wrap=tk.WORD)
		self.log.grid(row=row, column=0, columnspan=3, sticky=tk.NSEW, pady=(2, 0))
		frm.columnconfigure(1, weight=1)
		frm.rowconfigure(row, weight=1)

		for var in (
			self.series_var,
			self.powers_var,
			self.seed_var,
			self.reps_var,
			self.ngroups_var,
		):
			var.trace_add("write", lambda *_: self._refresh_plan())
		self._refresh_plan()

	def _browse(self) -> None:
		path = filedialog.askopenfilename(
			title="Fat Mark Points XML",
			filetypes=[("Mark Points", "*.xml *.gpl"), ("All", "*.*")],
		)
		if path:
			self.series_var.set(path)

	def _refresh_plan(self) -> None:
		try:
			series = self.series_var.get().strip()
			powers = parse_powers_field(self.powers_var.get())
			if not series or not Path(series).is_file():
				self.plan_var.set(f"powers {powers}  ·  choose a Fat XML")
				return
			pts = extract_unique_points(load_mark_points_file(series))
			self.points = pts
			ng_txt = self.ngroups_var.get().strip()
			ng = int(ng_txt) if ng_txt else None
			n_g = ng if ng is not None else n_groups_for_pool(len(pts))
			n_t = planned_trial_count(len(pts), powers, n_groups=n_g)
			wrap = n_g * GROUP_SIZE - len(pts)
			extra = (
				f"  ·  {wrap} wrap so every point is used"
				if wrap > 0
				else "  ·  every point used"
			)
			self.plan_var.set(
				f"{len(pts)} points → {n_g} groups of {GROUP_SIZE} × {len(powers)} powers "
				f"= {n_t} trials{extra}"
			)
		except Exception as exc:
			self.plan_var.set(str(exc))

	def _append_log(self, line: str) -> None:
		self.log.insert(tk.END, line.rstrip() + "\n")
		self.log.see(tk.END)

	def _on_run(self) -> None:
		if self._worker is not None and self._worker.is_alive():
			return
		try:
			series = self.series_var.get().strip()
			if not series or not Path(series).is_file():
				raise ValueError("choose a Fat Mark Points XML")
			powers = parse_powers_field(self.powers_var.get())
			pts = extract_unique_points(load_mark_points_file(series))
			ng_txt = self.ngroups_var.get().strip()
			ng = int(ng_txt) if ng_txt else None
			opt = apply_gui_overrides(
				self.cfg,
				series=series,
				powers=powers,
				seed=int(self.seed_var.get() or 0),
				repetitions=int(self.reps_var.get() or 1),
				stim_dff_min=float(self.thresh_var.get() or 0.0),
				n_points=len(pts),
				n_groups=ng,
			)
		except Exception as exc:
			messagebox.showerror("Cannot start", str(exc))
			return

		self.points = pts
		n_t = planned_trial_count(len(pts), powers, n_groups=int(opt["n_groups"]))
		if self.boards is not None:
			self.boards.close()
		self.boards = PowerBoards(
			powers,
			pts,
			stim_dff_min=float(opt["stim_dff_min"]),
			n_trials=n_t,
			show=True,
		)
		self._stop.clear()
		self.run_btn.configure(state=tk.DISABLED)
		self.stop_btn.configure(state=tk.NORMAL)
		self._append_log(
			f"start  {len(pts)} pts  groups={opt['n_groups']}  powers={powers}  "
			f"reps={opt['repetitions']}"
		)

		def work() -> None:
			from prairie_live.sync_loop import execute_session

			try:
				trials = execute_session(
					opt,
					on_trial=lambda row: self.q.put(("trial", dict(row))),
					show_candidate_gui=False,
					should_stop=self._stop.is_set,
				)
				self.q.put(("done", {"trials": trials, "opt": opt, "points": pts}))
			except Exception as exc:
				self.q.put(("error", str(exc)))

		self._worker = threading.Thread(target=work, daemon=True)
		self._worker.start()

	def _on_stop(self) -> None:
		self._stop.set()
		self._append_log("stop requested — finishing current trial")

	def _poll(self) -> None:
		try:
			while True:
				kind, payload = self.q.get_nowait()
				if kind == "trial":
					self._append_log(trial_log_line(payload))
					if self.boards is not None:
						self.boards.update(payload)
				elif kind == "done":
					self._finish_ok(payload)
				elif kind == "error":
					self.run_btn.configure(state=tk.NORMAL)
					self.stop_btn.configure(state=tk.DISABLED)
					self._append_log(f"error: {payload}")
					messagebox.showerror("Run failed", str(payload))
		except queue.Empty:
			pass
		self.root.after(80, self._poll)

	def _finish_ok(self, payload: dict) -> None:
		from prairie_live.candidate_view import show_candidate_picker
		from prairie_live.candidates import rank_candidate_points
		from prairie_live.sync_loop import _last_f0_from_trials
		from prairie_live.experiment_config import apply_stim_to_meta
		from prairie_live.markpoints import template_meta

		self.run_btn.configure(state=tk.NORMAL)
		self.stop_btn.configure(state=tk.DISABLED)
		trials = payload.get("trials") or []
		pts = payload.get("points") or self.points
		opt = payload.get("opt") or self.cfg
		self._append_log(f"done  {len(trials)} trials  — pick candidates")
		ranked = rank_candidate_points(
			trials, pts, stim_dff_min=float(opt.get("stim_dff_min") or 0.0)
		)
		fov = _last_f0_from_trials(trials)
		meta = apply_stim_to_meta(template_meta([]), opt)
		out = Path(opt.get("images_dir") or ".") 
		# Prefer the run folder from the last trial if present.
		for row in reversed(trials):
			paths = row.get("image_paths") or {}
			td = paths.get("trial_dir")
			if td:
				out = Path(td).parent
				break
		xml_path = out / "next_series.xml"
		try:
			show_candidate_picker(
				ranked, meta=meta, fov=fov, out_xml=xml_path, parent=self.root
			)
		except Exception as exc:
			self._append_log(f"candidate picker: {exc}")


if __name__ == "__main__":
	main()
