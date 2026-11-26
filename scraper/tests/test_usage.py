from datetime import date

import cost_report
import usage


def call(model, chain="Albert", attempt=1, tin=2000, tout=300):
    return {"model": model, "chain": chain, "attempt": attempt,
            "input_tokens": tin, "output_tokens": tout}


def test_record_sums_runs_of_one_day(tmp_path):
    day = date(2026, 11, 27)
    usage.record(tmp_path, day, [call("claude-haiku-5-5"),
                                 call("claude-haiku-5-5", attempt=2)])
    usage.record(tmp_path, day, [call("claude-haiku-5-5")])
    rows = __import__("json").loads(
        (tmp_path / "usage" / "2026-11-27.json").read_text("utf-8"))["rows"]
    assert rows == [{"chain": "Albert", "model": "claude-haiku-5-5",
                     "calls": 3, "pages": 2, "input_tokens": 6000,
                     "output_tokens": 900}]


def test_cost_uses_price_list():
    row = {"model": "claude-sonnet-5-5", "input_tokens": 1_000_000,
           "output_tokens": 100_000}
    assert usage.cost_usd(row) == 3.0
    assert usage.cost_usd({**row, "model": "neznamy"}) is None


def test_report_covers_last_days(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_report, "DATA_DIR", tmp_path)
    usage.record(tmp_path, date(2026, 11, 20), [call("claude-sonnet-5-5")])
    usage.record(tmp_path, date(2026, 11, 26),
                 [call("claude-haiku-5-5", tin=1_000_000, tout=0)])
    _, body = cost_report.build_report(date(2026, 11, 30), 7, 20.0)
    assert "Celkem za 7 dní: 2,00 Kč (0.100 USD)" in body
    assert "Dny s novými letáky: 1 z 7" in body
    assert "Albert: stránek 1, volání 1" in body
