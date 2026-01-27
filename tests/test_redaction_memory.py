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

        # 2. Verify the stored result object itself is redacted
        recorded_result = job.results[0]

        assert "[redacted]" in recorded_result.stdout
        assert secret_token not in recorded_result.stdout

        assert "[redacted]" in recorded_result.message
        assert secret_token not in recorded_result.message

    finally:
        with JOB_LOCK:
            JOBS.pop(job_id, None)
