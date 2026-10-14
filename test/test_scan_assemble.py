import numpy as np

from prairie_live.scan_assemble import ScanAssembler


def test_full_frame_passes_through():
	a = ScanAssembler()
	img = np.arange(32 * 40, dtype=np.uint16).reshape(32, 40)
	got = a.push(img)
	assert got.shape == (32, 40)
	np.testing.assert_array_equal(got, img)


def test_junk_1x1_is_none():
	a = ScanAssembler()
	assert a.push(np.array([[16]], dtype=np.uint16)) is None


def test_row_lines_fill_square():
	a = ScanAssembler()
	got = None
	for i in range(32):
		line = np.full((1, 32), i + 1, dtype=np.uint16)
		got = a.push(line)
		assert got is not None
		assert got.shape == (32, 32)
		assert int(got[i, 0]) == i + 1
	assert int(got[31, 31]) == 32


def test_column_strip_transposes_to_rows():
	a = ScanAssembler()
	col = np.arange(32, dtype=np.uint16).reshape(32, 1)
	got = a.push(col)
	assert got is not None
	assert got.shape == (32, 32)
	np.testing.assert_array_equal(got[0], np.arange(32, dtype=np.uint16))
