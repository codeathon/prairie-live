

def test_leftover_index_only_entry_does_not_set_timing():
	from prairie_live.markpoints import extract_unique_points, parse_mark_points, template_meta

	xml = """<PVSavedMarkPointSeriesElements Iterations="1" IterationDelay="0.00" CalcFunctMap="False">
  <PVMarkPointElement Repetitions="1" UncagingLaser="Monaco" UncagingLaserPower="160">
    <PVGalvoPointElement InitialDelay="2" InterPointDelay="0.26" Duration="10" SpiralRevolutions="8" Points="Custom Order (skip over missing indices)" Indices="48, 24, 54" SkipMissingIndices="True" />
  </PVMarkPointElement>
  <PVMarkPointElement Repetitions="3" UncagingLaser="Monaco" UncagingLaserPower="0.4">
    <PVGalvoPointElement InitialDelay="22.1" InterPointDelay="14" Duration="36" SpiralRevolutions="8" Points="Point 1" Indices="1" X="-2.86" Y="-3.62" IsSpiral="True" SpiralSize="0.1849" />
  </PVMarkPointElement>
  <PVMarkPointElement Repetitions="3" UncagingLaser="Monaco" UncagingLaserPower="0.4">
    <PVGalvoPointElement InitialDelay="2" InterPointDelay="14" Duration="36" SpiralRevolutions="8" Points="Point 2" Indices="2" X="-2.42" Y="-3.64" IsSpiral="True" SpiralSize="0.1849" />
  </PVMarkPointElement>
</PVSavedMarkPointSeriesElements>"""
	steps = parse_mark_points(xml)
	assert [p["id"] for p in extract_unique_points(steps)] == ["1", "2"]
	meta = template_meta(steps)
	assert meta["duration"] == 36.0
	assert meta["repetitions"] == 3
	assert meta["initial_delay"] == "22.1"
	assert meta["inter_point_delay"] == "14"
	assert meta["step_delay"] == "2"
