from unittest.mock import patch, MagicMock
from panel.app import apply_patch_action, execute_publish, ApplyPatchReq, PublishReq

def test_apply_patch_empty_error_kind():
    # Mock get_repo to avoid checking file system
    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.check_branch_guard") as mock_guard:

        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")
        mock_guard.return_value = None # No error

        req = ApplyPatchReq(repo="metarepo", patch="   ") # Empty patch
        result, code = apply_patch_action(req)

        assert code == 400
        assert result.error_kind == "invalid_input"
        assert result.message == "Patch is empty"

def test_publish_invalid_branch_error_kind():
    # Test execute_publish directly
    # It requires job_id, correlation_id, req
    with patch("panel.app.get_repo") as mock_get_repo:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishReq(repo="metarepo", branch="invalid\\branch")

        # We need to capture the result recorded.
        # execute_publish calls record_job_result.
        with patch("panel.app.record_job_result") as mock_record:
             execute_publish("job-1", "corr-1", "metarepo", req)

             # Verify record_job_result was called with error
             args, _ = mock_record.call_args
             job_id, result = args

             assert job_id == "job-1"
             assert result.error_kind == "invalid_input"
             assert result.message == "Invalid branch name"
