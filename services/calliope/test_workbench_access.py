"""Historical guided receipts remain readable after executor retirement."""
import pytest
import workbench_access


@pytest.mark.parametrize('operation', ['events', 'recordings', 'guided_snapshot'])
def test_historical_evidence_is_not_read_for_another_owner(operation):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            assert 'calliope_workbenches' in query and 'owner_email=%s' in query
            assert params[1] == 'other@example.com'
            return self
        def fetchone(self): return None
    with pytest.raises(workbench_access.WorkbenchError) as error:
        getattr(workbench_access, operation)(Connection, 'other@example.com', '11111111-1111-4111-8111-111111111111')
    assert error.value.code == 'WORKBENCH_NOT_FOUND'


def test_guided_snapshot_preserves_correction_and_verification_history():
    run_id = "11111111-1111-4111-8111-111111111111"
    original_id = "22222222-2222-4222-8222-222222222222"
    corrected_id = "33333333-3333-4333-8333-333333333333"

    def event(sequence, event_type, payload):
        return {
            "id": f"{sequence:08d}-0000-4000-8000-000000000000",
            "sequence": sequence,
            "event_type": event_type,
            "payload": {"run_id": run_id, **payload},
        }

    events = [
        event(1, "guided.run.started", {"goal": "Reconcile the queue", "workflow_id": run_id}),
        event(2, "guided.observed", {"screenshot": {"url": "/before.png"}}),
        event(3, "guided.action.proposed", {"proposal_id": original_id, "action": {"kind": "click", "x": 1, "y": 2}}),
        event(4, "guided.action.corrected", {"proposal_id": corrected_id, "replaces_proposal_id": original_id, "action": {"kind": "click", "x": 4, "y": 5}}),
    ]
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["phase"] == "approve"
    assert snapshot["proposal"]["payload"]["proposal_id"] == corrected_id

    events.extend([
        event(5, "guided.action.approved", {"proposal_id": corrected_id}),
        event(6, "guided.action.executed", {"proposal_id": corrected_id, "after_screenshot": {"url": "/after.png"}}),
    ])
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["phase"] == "verify"
    events.append(event(7, "guided.step.verified", {"proposal_id": corrected_id, "passed": True}))
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["phase"] == "observe"
    assert len(snapshot["steps"]) == 1
    assert snapshot["steps"][0]["verification"]["payload"]["passed"] is True

    events.append(event(8, "guided.recovery.noted", {"proposal_id": corrected_id, "outcome": "resolved"}))
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["adaptations"][0]["payload"]["outcome"] == "resolved"



def test_guided_snapshot_tracks_shadow_replay_terminal_states():
    run_id = "44444444-4444-4444-8444-444444444444"
    workflow_id = "55555555-5555-4555-8555-555555555555"
    proposal_id = "66666666-6666-4666-8666-666666666666"

    def event(sequence, event_type, payload):
        return {
            "id": f"{sequence:08d}-0000-4000-8000-000000000000",
            "sequence": sequence,
            "event_type": event_type,
            "payload": {"run_id": run_id, **payload},
        }

    events = [
        event(
            1,
            "guided.run.started",
            {
                "workflow_id": workflow_id,
                "procedure_run_id": "77777777-7777-4777-8777-777777777777",
                "revision": 2,
                "goal": "Reconcile the queue",
                "mode": "shadow_replay",
                "replay_steps": [{"action": {"kind": "wait", "milliseconds": 10}}],
            },
        ),
        event(2, "guided.observed", {"screenshot": {"url": "/before.png"}}),
        event(
            3,
            "guided.action.proposed",
            {"proposal_id": proposal_id, "step_index": 0, "action": {"kind": "wait", "milliseconds": 10}},
        ),
        event(4, "guided.action.approved", {"proposal_id": proposal_id}),
        event(5, "guided.action.executed", {"proposal_id": proposal_id}),
        event(6, "guided.step.verified", {"proposal_id": proposal_id, "passed": True}),
        event(
            7,
            "guided.replay.completed",
            {"workflow_id": workflow_id, "revision": 2, "passed": True},
        ),
    ]
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["phase"] == "replay_complete"
    assert snapshot["run"]["mode"] == "shadow_replay"
    assert snapshot["run"]["revision"] == 2
    assert snapshot["run"]["procedure_run_id"] == "77777777-7777-4777-8777-777777777777"

    events[-1] = event(
        7,
        "guided.replay.paused",
        {"workflow_id": workflow_id, "revision": 2, "reason": "visual_divergence"},
    )
    assert workbench_access._guided_snapshot_from_events(events)["phase"] == "replay_paused"



def test_shadow_replay_divergence_enters_recovery_instead_of_terminal_pause():
    run_id = "88888888-8888-4888-8888-888888888888"
    workflow_id = "99999999-9999-4999-8999-999999999999"
    proposal_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    events = [
        {"id": "1", "sequence": 1, "event_type": "guided.run.started", "payload": {"run_id": run_id, "workflow_id": workflow_id, "mode": "shadow_replay", "replay_steps": [{"action": {"kind": "click", "x": 1, "y": 1}}]}},
        {"id": "2", "sequence": 2, "event_type": "guided.observed", "payload": {"run_id": run_id}},
        {"id": "3", "sequence": 3, "event_type": "guided.action.proposed", "payload": {"run_id": run_id, "proposal_id": proposal_id, "step_index": 0}},
        {"id": "4", "sequence": 4, "event_type": "guided.action.approved", "payload": {"run_id": run_id, "proposal_id": proposal_id}},
        {"id": "5", "sequence": 5, "event_type": "guided.action.executed", "payload": {"run_id": run_id, "proposal_id": proposal_id}},
        {"id": "6", "sequence": 6, "event_type": "guided.step.verified", "payload": {"run_id": run_id, "proposal_id": proposal_id, "passed": False}},
        {"id": "7", "sequence": 7, "event_type": "guided.recovery.requested", "payload": {"run_id": run_id, "chain_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "step_index": 0, "next_attempt": 1}},
    ]
    snapshot = workbench_access._guided_snapshot_from_events(events)
    assert snapshot["phase"] == "observe"
    assert snapshot["run"]["recovery_request"]["payload"]["next_attempt"] == 1
    assert snapshot["run"]["terminal"] is None
