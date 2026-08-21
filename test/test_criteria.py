from pathlib import Path

from prairie_live.criteria import CriteriaModel
from prairie_live.detect import FEATURE_NAMES, Blob


def _blob(i, mean, snr, label=None):
	feats = {k: 0.0 for k in FEATURE_NAMES}
	feats["mean"] = mean
	feats["snr"] = snr
	feats["max"] = mean + 10
	feats["circularity"] = 0.9
	b = Blob(id=i, y=10, x=10, radius_px=5, response=snr, features=feats, label=label)
	return b


def test_fit_is_noop_until_both_classes_and_min_labels(tmp_path: Path):
	m = CriteriaModel(min_labels=8, out_dir=tmp_path)
	for i in range(8):
		assert m.add(_blob(i, mean=50, snr=5), 1) is False
	assert m.clf is None
	# still one class
	assert m.add(_blob(8, mean=50, snr=5), 1) is False


def test_model_ranks_bright_above_dim(tmp_path: Path):
	m = CriteriaModel(min_labels=8, out_dir=tmp_path)
	n = 0
	for i in range(5):
		m.add(_blob(n, mean=80, snr=8), 1)
		n += 1
		m.add(_blob(n, mean=10, snr=0.5), 0)
		n += 1
	assert m.clf is not None
	bright = _blob(99, mean=85, snr=9)
	dim = _blob(100, mean=8, snr=0.2)
	m.score_blobs([bright, dim])
	assert bright.p_hat is not None and dim.p_hat is not None
	assert bright.p_hat > dim.p_hat
	ranked = m.ranked_unlabeled([dim, bright])
	assert ranked[0] is bright
	assert (tmp_path / "session.jsonl").exists()
	assert "snr" in (tmp_path / "criteria.txt").read_text()
