import time

from fastapi.testclient import TestClient

from app.main import app


def test_end_to_end():
    with TestClient(app) as c:
        for _ in range(300):  # the lifespan kicks off the first refresh in the background
            if c.get("/healthz").json()["last_refresh"]:
                break
            time.sleep(0.1)
        assert c.get("/healthz").json()["with_data"] > 50
        board = c.get("/api/funds").json()["funds"]
        assert board[0]["drawdown"] <= board[1]["drawdown"]
        d = c.get("/api/funds/TQQQ").json()
        assert d["summary"]["price"] == 42.75 and d["episodes"] and d["forecast"]["available"]
        assert any(t["price"] == 25 for t in d["targets"])          # seeded "all the money" target
        r = c.post("/api/agent/forecasts", json={"agent": "test", "ticker": "TQQQ", "stance": "accumulate",
                                                  "target_price": 60, "rationale": "bounce"})
        assert r.status_code == 201
        assert c.get("/api/agent/forecasts?ticker=TQQQ").json()[0]["agent"] == "test"
        assert c.get("/api/agent/context/TQQQ").json()["tier"]["key"] == "shock"
        assert len(c.get("/api/agent/manifest").json()["tools"]) == 4
        assert c.post("/api/agent/ask", json={"ticker": "TQQQ"}).status_code == 502  # no LLM configured
