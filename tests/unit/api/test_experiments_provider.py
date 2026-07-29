import csv
import json

import pytest

from ra_agent.api import experiments_provider


def test_experiment_provider_uses_frozen_v2_result_root() -> None:
    assert experiments_provider._raw_dir() == (
        experiments_provider.EXPERIMENTS_DIR / "v2" / "results" / "raw"
    )


@pytest.mark.asyncio
async def test_experiment_provider_rejects_path_traversal_and_reads_raw_formats(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(experiments_provider, "_raw_dir", lambda: tmp_path)
    (tmp_path / "records.json").write_text(json.dumps([{"case_id": "json"}]), encoding="utf-8")
    (tmp_path / "records.jsonl").write_text('{"case_id": "jsonl"}\n', encoding="utf-8")
    with (tmp_path / "records.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id"])
        writer.writeheader()
        writer.writerow({"case_id": "csv"})

    listing = await experiments_provider.list_results()
    traversal = await experiments_provider.get_result("../records.json")
    jsonl_result = await experiments_provider.get_result("records.jsonl")
    csv_result = await experiments_provider.get_result("records.csv")

    assert {item["name"] for item in listing.data or []} == {
        "records.json",
        "records.jsonl",
        "records.csv",
    }
    assert traversal.data == {"error": "not_found", "filename": "../records.json"}
    assert jsonl_result.data == {"filename": "records.jsonl", "results": [{"case_id": "jsonl"}]}
    assert csv_result.data == {"filename": "records.csv", "results": [{"case_id": "csv"}]}
