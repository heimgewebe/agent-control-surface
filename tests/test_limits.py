from panel.app import (
    record_job_result,
    JobState,
    ActionResult,
    JOBS,
    JOB_LOCK,
    MAX_STDOUT_CHARS,
    MAX_LOG_LINE_CHARS,
    MAX_JOB_LOG_LINES
)
import json

def test_record_job_result_caps():
    job_id = "test-job-caps"
    # Setup job
    with JOB_LOCK:
        JOBS[job_id] = JobState(job_id=job_id, status="running")

    # 1. Test stdout truncation
    long_stdout = "a" * (MAX_STDOUT_CHARS + 100)
    result = ActionResult(
        ok=True, action="test", repo="r", correlation_id="c", ts="t",
        stdout=long_stdout
    )

    record_job_result(job_id, result)

    job = JOBS[job_id]
    assert len(job.results) == 1
    recorded_result = job.results[0]

    # Check truncation
    assert len(recorded_result.stdout) < len(long_stdout)
    assert recorded_result.stdout.endswith("... (truncated)")
    assert len(recorded_result.stdout) == MAX_STDOUT_CHARS + len("... (truncated)")

    # 2. Test log line truncation
    # Make a result that produces a very long JSON line (e.g. huge message)
    # Even if stdout is truncated, message might be long? Or we force it.
    # Just verify that if the JSON is long, the log line is truncated.
    # Construct a result where the serialized form is long.
    # We already truncated stdout, so we need something else to be long?
    # Or just rely on stdout being long (50k chars) which is > MAX_LOG_LINE_CHARS (4000).

    log_line = job.log_lines[0]
    assert len(log_line) <= MAX_LOG_LINE_CHARS + len("... (truncated)")
    assert log_line.endswith("... (truncated)")

    # 3. Test max log lines
    # Fill up log lines
    # We already have 1. Let's add MAX_JOB_LOG_LINES.
    # Total should be MAX_JOB_LOG_LINES (oldest dropped).

    # We need to add enough to trigger drop.
    # Current count: 1.
    # We want final count: MAX_JOB_LOG_LINES.
    # So we need to add MAX_JOB_LOG_LINES more. Total added: MAX_JOB_LOG_LINES + 1.
    # Resulting size: MAX_JOB_LOG_LINES.

    for i in range(MAX_JOB_LOG_LINES):
        res = ActionResult(
            ok=True, action=f"test-{i}", repo="r", correlation_id="c", ts="t"
        )
        record_job_result(job_id, res)

    assert len(job.log_lines) == MAX_JOB_LOG_LINES
    # The first one (truncated stdout) should be gone.
    # The last one should be "test-{MAX_JOB_LOG_LINES-1}"

    last_entry = json.loads(job.log_lines[-1])
    assert last_entry["action"] == f"test-{MAX_JOB_LOG_LINES-1}"

    # Check first entry in current list
    first_entry = json.loads(job.log_lines[0])
    # We added 0..999 (1000 items) + original 1 = 1001 items.
    # Expectation: original dropped. 0 should be first?
    # If we keep 1000 items.
    # Items added: Original, 0, 1, ..., 999.
    # Remaining: 0, ..., 999.
    assert first_entry["action"] == "test-0"

    print("Limits tests passed.")
