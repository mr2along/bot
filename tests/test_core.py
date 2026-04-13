import math
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from app import compute_signal, futures_pnl, recommend_position_size


def _mock_data(n=60, start=1900.0, step=0.2):
    t = list(range(1, n + 1))
    c = [start + i * step for i in range(n)]
    o = [x - 0.2 for x in c]
    h = [x + 0.4 for x in c]
    l = [x - 0.4 for x in c]
    v = [1000 + i for i in range(n)]
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_futures_pnl_long():
    out = futures_pnl(side="LONG", entry=1900, exit_price=1902, contracts=2)
    assert out["points"] == 2
    assert out["gross_pnl_vnd"] > 0


def test_recommend_position_size_non_negative():
    out = recommend_position_size(price=1900, atr=2.0, account_balance=100_000_000)
    assert out["contracts"] >= 0
    assert out["risk_per_contract"] > 0


def test_compute_signal_shape():
    out = compute_signal(_mock_data())
    assert out["signal"] in {"BUY", "SELL", "HOLD", "WAIT"}
    assert isinstance(out["confidence"], float)
    assert "reason" in out


def test_compute_signal_wait_when_short_history():
    out = compute_signal(_mock_data(n=20))
    assert out["signal"] == "WAIT"
    assert "Warming up" in out["reason"]


def test_compute_signal_risk_when_actionable():
    out = compute_signal(_mock_data(n=80, start=1800, step=0.5))
    if out["signal"] in {"BUY", "SELL", "HOLD"}:
        # HOLD can still carry risk object in current design.
        assert "risk" in out
        if out["risk"] is not None:
            assert math.isfinite(out["risk"]["atr"])
