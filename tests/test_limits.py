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

    try:
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
        log_line = job.log_lines[0]
        assert len(log_line) <= MAX_LOG_LINE_CHARS + len("... (truncated)")
        assert log_line.endswith("... (truncated)")

        # 3. Test max log lines
        for i in range(MAX_JOB_LOG_LINES):
            res = ActionResult(
                ok=True, action=f"test-{i}", repo="r", correlation_id="c", ts="t"
            )
            record_job_result(job_id, res)

        assert len(job.log_lines) == MAX_JOB_LOG_LINES

        last_entry = json.loads(job.log_lines[-1])
        assert last_entry["action"] == f"test-{MAX_JOB_LOG_LINES-1}"

        # Check first entry in current list
        first_entry = json.loads(job.log_lines[0])
        assert first_entry["action"] == "test-0"

        print("Limits tests passed.")
    finally:
        with JOB_LOCK:
            JOBS.pop(job_id, None)
