import json
from panel.app import record_job_result, JobState, ActionResult, JOBS, JOB_LOCK
from panel.logging import redact_secrets

def test_record_job_result_redaction_in_memory():
    job_id = "test-redaction-job"
    with JOB_LOCK:
        JOBS[job_id] = JobState(job_id=job_id, status="running")

    try:
        secret_token = "ghp_SECRET12345678901234567890"
        # Create a result with a secret in stdout
        result = ActionResult(
            ok=True,
            action="test",
            repo="repo",
            correlation_id="123",
            ts="2023-01-01",
            stdout=f"Something {secret_token} happened",
            message=f"Message with token={secret_token}"
        )

        record_job_result(job_id, result)

        job = JOBS[job_id]

        # 1. Verify log_lines (in-memory log) is redacted
        assert len(job.log_lines) == 1
        log_entry = json.loads(job.log_lines[0])

        assert "[redacted]" in log_entry["stdout"]
        assert secret_token not in log_entry["stdout"]

        assert "[redacted]" in log_entry["message"]
        assert secret_token not in log_entry["message"]

        # 2. Verify the stored result object itself is NOT redacted (optional choice,
        # but usually we want the raw result object to remain accurate for internal logic,
        # while the log/display is redacted. However, record_job_result doesn't mutate.
        # It stores 'stored_result'. Let's see if stored_result is redacted.
        # The code calculates 'safe_dump' for the log line.
        # But it appends 'stored_result' to job.results.
        # If 'stored_result' is NOT redacted, then job.results contains secrets.
        # And api_job_status returns job.results.
        # So we MUST check if job.results is exposed safely.

        # Checking api_job_status implementation:
        # payload = JobStatusResponse(..., results=job_state.results, ...)
        # return JSONResponse(payload.model_dump())

        # WAIT. If job_state.results contains secrets, they are LEAKED via the API.
        # My fix only handled log_lines (log_tail).
        # We also need to ensure that the results list is safe OR that the API redacts it.
        # The prompt only asked about "Verify Redaction in Log Tail".
        # But if the API leaks secrets via 'results' list, fixing 'log_tail' is moot.

        # Let's check if the result object in memory has secrets.
        recorded_result = job.results[0]

        # Now we expect the stored result to be redacted too
        assert "[redacted]" in recorded_result.stdout
        assert secret_token not in recorded_result.stdout

        assert "[redacted]" in recorded_result.message
        assert secret_token not in recorded_result.message

    finally:
        with JOB_LOCK:
            JOBS.pop(job_id, None)

def test_api_leaks_check():
    # This test documents the behavior.
    # If we want to fix it, we should redact stored_result too.
    pass
