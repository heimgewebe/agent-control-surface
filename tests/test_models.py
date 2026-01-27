from panel.app import JobState, ActionResult

def test_job_state_smoke():
    """Smoke test to ensure JobState can be instantiated and serialized with results."""
    # Create a dummy ActionResult
    result = ActionResult(
        ok=True,
        action="test",
        repo="test-repo",
        correlation_id="123",
        ts="2023-01-01T00:00:00Z"
    )

    # Create JobState with the result
    job = JobState(
        job_id="job-1",
        status="done",
        results=[result]
    )

    # Check that we can dump it to JSON (this triggers Pydantic serialization)
    data = job.model_dump()
    assert data["job_id"] == "job-1"
    assert len(data["results"]) == 1
    assert data["results"][0]["action"] == "test"

    # Also check JSON string
    json_str = job.model_dump_json()
    assert "test-repo" in json_str

    print("JobState smoke test passed.")
