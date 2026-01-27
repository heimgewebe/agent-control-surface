from panel.app import is_valid_branch_name

def test_valid_branch_names():
    assert is_valid_branch_name("feature/abc")
    assert is_valid_branch_name("bugfix-123")
    assert is_valid_branch_name("main")
    assert is_valid_branch_name("v1.0.0")
    assert is_valid_branch_name("user/name/repo")

def test_invalid_branch_names():
    # Empty or space
    assert not is_valid_branch_name("")
    assert not is_valid_branch_name("feature abc")

    # Invalid characters
    assert not is_valid_branch_name("feature\\abc")
    assert not is_valid_branch_name("feature:abc")
    assert not is_valid_branch_name("feature?abc")
    assert not is_valid_branch_name("feature*abc")
    assert not is_valid_branch_name("feature[abc")
    assert not is_valid_branch_name("feature@{abc")

    # Git restrictions
    assert not is_valid_branch_name("-start-dash")
    assert not is_valid_branch_name("end-lock.lock")
    assert not is_valid_branch_name("path/../traversal")
    assert not is_valid_branch_name("feature..abc")
    assert not is_valid_branch_name("feature//abc")
    assert not is_valid_branch_name("feature/./abc")
    assert not is_valid_branch_name("@")

    # Backslash specifically (was inconsistent before)
    assert not is_valid_branch_name("foo\\bar")
