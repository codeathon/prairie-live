"""In-session logistic model: coefficients are the scientist's criteria."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from prairie_live.detect import FEATURE_NAMES, Blob
from prairie_live.features import feature_vector


class CriteriaModel:
	def __init__(self, min_labels: int = 8, out_dir: str | Path | None = None):
		self.min_labels = min_labels
		self.rows: list[dict] = []
		self.clf = None
		self._mean: np.ndarray | None = None
		self._std: np.ndarray | None = None
		self.out_dir = Path(out_dir) if out_dir else None
		if self.out_dir:
			self.out_dir.mkdir(parents=True, exist_ok=True)

	def ready(self) -> bool:
		labs = [r["label"] for r in self.rows]
		return len(labs) >= self.min_labels and (0 in labs) and (1 in labs)

	def add(self, blob: Blob, label: int) -> bool:
		blob.label = label
		rec = {
			"ts": time.time(),
			"id": blob.id,
			"label": int(label),
			"p_hat": blob.p_hat,
			"features": dict(blob.features),
		}
		self.rows.append(rec)
		self._append_jsonl(rec)
		return self.fit()

	def fit(self) -> bool:
		if not self.ready():
			self.clf = None
			return False
		X = np.stack([feature_vector(r["features"]) for r in self.rows])
		y = np.array([r["label"] for r in self.rows], dtype=np.int32)
		self._mean = X.mean(axis=0)
		std = X.std(axis=0)
		std[std < 1e-8] = 1.0
		self._std = std
		from sklearn.linear_model import LogisticRegression

		clf = LogisticRegression(max_iter=400, solver="lbfgs")
		clf.fit(self._scale(X), y)
		self.clf = clf
		line = self.format_weights()
		print(line)
		self._write_criteria(line)
		return True

	def predict_proba(self, feats: dict[str, float]) -> float | None:
		if self.clf is None or self._mean is None:
			return None
		x = self._scale(feature_vector(feats).reshape(1, -1))
		return float(self.clf.predict_proba(x)[0, 1])

	def score_blobs(self, blobs: list[Blob]) -> None:
		for b in blobs:
			b.p_hat = self.predict_proba(b.features)

	def ranked_unlabeled(self, blobs: list[Blob]) -> list[Blob]:
		open_ = [b for b in blobs if b.label is None]
		if self.clf is None:
			open_.sort(key=lambda b: b.response, reverse=True)
			return open_
		self.score_blobs(open_)
		open_.sort(key=lambda b: b.p_hat if b.p_hat is not None else -1.0, reverse=True)
		return open_

	def format_weights(self) -> str:
		if self.clf is None:
			return "criteria: (model off)"
		coef = self.clf.coef_[0]
		parts = [f"{n} {c:+.2f}" for n, c in zip(FEATURE_NAMES, coef)]
		return "criteria: " + "  ".join(parts)

	def _scale(self, X: np.ndarray) -> np.ndarray:
		return (X - self._mean) / self._std

	def _append_jsonl(self, rec: dict) -> None:
		if self.out_dir is None:
			return
		path = self.out_dir / "session.jsonl"
		with path.open("a", encoding="utf-8") as f:
			f.write(json.dumps(rec) + "\n")

	def _write_criteria(self, line: str) -> None:
		if self.out_dir is None:
			return
		(self.out_dir / "criteria.txt").write_text(line + "\n", encoding="utf-8")
