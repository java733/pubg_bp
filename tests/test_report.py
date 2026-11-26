from tools.report import sample_points, summarize


def test_summarize_counts_states():
    s = summarize([{"state": "LOBBY"}, {"state": "LOBBY"}, {"state": "UNKNOWN"}])
    assert s["frames"] == 3
    assert s["state_counts"]["LOBBY"] == 2


def test_sample_points_returns_original_rows():
    rows = [{"state": "X", "i": i} for i in range(10)]
    picked = sample_points(rows, 3)
    assert len(picked) == 3
    assert all(p in rows for p in picked)
