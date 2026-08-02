from typing import cast

from scripts.verify_final_experiments import verify


def test_fixed_formal_experiments_reconstruct_from_raw() -> None:
    report = cast(dict[str, dict[str, dict[str, int]]], verify())
    assert report["families"]["Safety"]["rows"] == 225
    assert report["families"]["Graph"]["rows"] == 18
    assert report["families"]["Rollback"]["rows"] == 105
