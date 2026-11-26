from tools.doctor import render_report


def test_render_report_includes_fail_and_advice():
    out = render_report([
        {"name": "python", "ok": True, "detail": "3.12.6"},
        {"name": "window", "ok": False, "detail": "not found", "advice": "请确认 PUBG 已启动"},
    ])
    assert "PASS" in out
    assert "FAIL" in out
    assert "请确认 PUBG 已启动" in out
