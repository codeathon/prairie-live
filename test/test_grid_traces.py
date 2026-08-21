import numpy as np

from prairie_live.grid_traces import TraceBuffer, tile_means


def test_tile_means_unique_2x2_blocks():
	# 16x16 / 8 = 2 px tiles; each 2x2 block is a distinct constant.
	img = np.zeros((16, 16), dtype=np.float32)
	want = np.zeros((8, 8), dtype=np.float32)
	for r in range(8):
		for c in range(8):
			val = float(r * 10 + c)
			img[2 * r : 2 * r + 2, 2 * c : 2 * c + 2] = val
			want[r, c] = val
	got = tile_means(img, grid=8)
	np.testing.assert_allclose(got, want)


def test_tile_means_crops_leftover_edges():
	# 17x17 must not stretch; leftover row/col are ignored.
	img = np.zeros((17, 17), dtype=np.float32)
	img[:16, :16] = 5.0
	img[16, :] = 999.0
	img[:, 16] = 999.0
	got = tile_means(img, grid=8)
	np.testing.assert_allclose(got, np.full((8, 8), 5.0))


def test_tile_means_undersized_frame_is_zeros():
	got = tile_means(np.ones((4, 4)), grid=8)
	np.testing.assert_array_equal(got, np.zeros((8, 8)))


def test_buffer_rolls_and_keeps_window():
	n = 10
	buf = TraceBuffer(n, grid=8)
	for i in range(n + 5):
		tiles = np.full((8, 8), float(i))
		buf.push(tiles)
	assert len(buf) == n
	out = buf.as_array()
	assert out.shape == (n, 8, 8)
	# Oldest kept sample is i=5, newest is i=14.
	assert out[0, 0, 0] == 5.0
	assert out[-1, 0, 0] == 14.0
