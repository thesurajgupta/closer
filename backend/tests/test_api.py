"""The HTTP surface. Nothing here may bypass the policy engine."""

import time


def _run(client):
    client.post("/api/run")
    for _ in range(100):
        latest = client.get("/api/runs/latest").json()
        if latest["active"]["state"] != "running":
            return latest
        time.sleep(0.05)
    raise AssertionError("run never finished")


def test_health(api_client):
    body = api_client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mode"] == "demo"


def test_state_before_any_run(api_client):
    state = api_client.get("/api/state").json()
    assert state["demo"] is True
    assert state["inbox_counts"]["documents"] >= 15
    assert state["metrics"]["disclaimer"].startswith("Illustrative")


def test_run_then_state(api_client):
    latest = _run(api_client)
    assert latest["run"]["status"] == "COMPLETED"
    assert len(latest["activity"]) > 20
    assert len(latest["tools"]) > 40

    state = api_client.get("/api/state").json()
    assert state["metrics"]["needs_you"] >= 1
    assert state["metrics"]["closed"] >= 2
    assert len(state["loops"]) >= 7
    # Decisions worth interrupting for come first.
    assert state["decisions"][0]["notify_now"] is True


def test_loop_detail_carries_evidence_timeline_and_policy(api_client):
    _run(api_client)
    state = api_client.get("/api/state").json()
    loop_id = next(l["id"] for l in state["loops"] if l["status"] == "HUMAN_DECISION")
    detail = api_client.get(f"/api/loops/{loop_id}").json()

    assert detail["evidence"]
    assert detail["timeline"]
    assert detail["plans"][-1]["policy"]["rule_id"]
    assert detail["approval"]["what_i_found"]


def test_evidence_resolves_to_its_source_document(api_client):
    _run(api_client)
    state = api_client.get("/api/state").json()
    loop_id = next(l["id"] for l in state["loops"] if l["evidence_count"] > 0)
    detail = api_client.get(f"/api/loops/{loop_id}").json()
    item = next(e for e in detail["evidence"] if e["source_type"] == "document")

    resolved = api_client.get(f"/api/evidence/{item['id']}").json()
    assert resolved["source"]["title"]
    assert resolved["source"]["text"]


def test_approval_flow_over_http(api_client):
    _run(api_client)
    decisions = api_client.get("/api/decisions").json()["decisions"]
    warranty = next(d for d in decisions if "Warranty" in d["title"])

    result = api_client.post(f"/api/decisions/{warranty['plan_id']}",
                             json={"decision": "approve"}).json()
    assert result["ok"] is True
    assert result["verification"]["status"] == "CONFIRMED"

    # The same approval cannot be replayed.
    again = api_client.post(f"/api/decisions/{warranty['plan_id']}", json={"decision": "approve"})
    assert again.status_code == 409


def test_a_bad_decision_verb_is_rejected(api_client):
    _run(api_client)
    plan_id = api_client.get("/api/decisions").json()["decisions"][0]["plan_id"]
    assert api_client.post(f"/api/decisions/{plan_id}", json={"decision": "just do it"}).status_code == 422


def test_settings_round_trip_and_change_behaviour(api_client):
    body = api_client.get("/api/settings").json()
    settings = body["settings"]
    assert "Send external messages" in body["summary"]["ask_first"]

    settings["external_messages"] = "auto"
    updated = api_client.put("/api/settings", json=settings).json()
    assert updated["settings"]["external_messages"] == "auto"

    latest = api_client.get("/api/state").json()
    assert "Send external messages" in latest["policy_summary"]["automatically"]


def test_widening_external_messages_removes_that_approval(api_client):
    settings = api_client.get("/api/settings").json()["settings"]
    settings["external_messages"] = "auto"
    api_client.put("/api/settings", json=settings)
    _run(api_client)

    decisions = api_client.get("/api/decisions").json()["decisions"]
    assert not any("Warranty" in d["title"] for d in decisions), \
        "with external messages set to auto, the claim should not need approval"


def test_run_report_is_downloadable(api_client):
    latest = _run(api_client)
    run_id = latest["run"]["run_id"]
    response = api_client.get(f"/api/runs/{run_id}/report")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    report = response.json()
    assert report["counts"]["tool_calls"] > 40
    assert report["data_notice"].startswith("All records are synthetic")


def test_architecture_endpoint_describes_the_real_system(api_client):
    arch = api_client.get("/api/architecture").json()
    assert len(arch["agents"]) == 6
    assert len(arch["tools"]) >= 25
    assert "ISSUE_PAYMENT" in arch["denied_in_demo"]
    assert set(arch["transitions"]) == set(arch["states"])


def test_inbox_shows_the_messy_starting_state(api_client):
    _run(api_client)
    inbox = api_client.get("/api/inbox").json()
    assert len(inbox["messages"]) >= 13
    assert any(m["quarantined"] for m in inbox["messages"])
    assert any(d["stale"] for d in inbox["documents"])


def test_reset_returns_to_a_fresh_week(api_client):
    _run(api_client)
    assert api_client.post("/api/demo/reset").json()["ok"] is True
    state = api_client.get("/api/state").json()
    assert state["metrics"]["closed"] <= 1  # only the seeded history remains
    assert state["last_run"] is None
    assert state["metrics"]["needs_you"] == 0
    assert len(api_client.get("/api/inbox").json()["messages"]) >= 13


def test_missing_resources_404(api_client):
    assert api_client.get("/api/loops/nope").status_code == 404
    assert api_client.get("/api/evidence/nope").status_code == 404
    assert api_client.get("/api/runs/nope").status_code == 404
