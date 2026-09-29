from ra_agent.execution import _temp_files


def test_internal_artifact_names_are_bounded_and_domain_separated() -> None:
    request_id = "request-" + "x" * 512

    commit = _temp_files.transaction_artifact_name(request_id, purpose="commit")
    quarantine = _temp_files.transaction_artifact_name(
        request_id,
        purpose="quarantine",
    )
    restore = _temp_files.transaction_artifact_name(request_id, purpose="restore")

    assert commit.startswith(".aegis-c-")
    assert quarantine.startswith(".aegis-q-")
    assert restore.startswith(".aegis-r-")
    assert len(commit) <= 42
    assert len(quarantine) <= 42
    assert len(restore) <= 42
    assert len({commit, quarantine, restore}) == 3
    assert request_id not in commit
    assert request_id not in quarantine
    assert request_id not in restore
