"""Prepare 200 synthetic review candidates without changing frozen pilot data.
Variants keep their original task group: parameter changes are not independent tasks.
No experiments or human approvals are fabricated.
"""

import csv
import hashlib
import json
from pathlib import Path
from .dataset import load

ROOT = Path(__file__).resolve().parent


def main():
    out = ROOT / "candidates"
    out.mkdir(exist_ok=True)
    cases = []
    for case in load():
        for variant in range(5):
            original = case["case_id"]
            old_device = (
                f"synthetic-device-{original.split('-')[-2]}-{original.split('-')[-1]}"
            )
            device = f"review-device-{original.split('-')[-2]}-{original.split('-')[-1]}-{variant}"
            encoded = (
                json.dumps(case, ensure_ascii=False)
                .replace(old_device, device)
                .replace(original, original + f"-candidate-{variant}")
            )
            item = json.loads(encoded)
            item.update(
                schema_version="intent-review-candidate-v1",
                parent_case_id=original,
                source="synthetic parameter variant of inspected pilot; not unseen formal evidence",
            )
            item["review"] = {
                "status": "pending_independent_review",
                "reviewers": [],
                "disagreements": [],
            }
            cases.append(item)
    data = out / "review-candidates.jsonl"
    data.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
        encoding="utf-8",
    )
    review = out / "review.csv"
    if not review.exists():
        with review.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "case_id",
                    "template_group",
                    "split",
                    "proposed_first_deviation",
                    "status",
                    "reviewer_1",
                    "reviewer_1_verdict",
                    "reviewer_2",
                    "reviewer_2_verdict",
                    "disagreement",
                    "resolution",
                    "evidence_ref",
                    "reviewed_at",
                ],
            )
            writer.writeheader()
            for c in cases:
                writer.writerow(
                    {
                        "case_id": c["case_id"],
                        "template_group": c["template_group"],
                        "split": c["split"],
                        "proposed_first_deviation": c["gold"]["first_deviation"],
                        "status": "pending_independent_review",
                    }
                )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "candidate_count": len(cases),
                "task_groups": 4,
                "dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                "formal_test_eligible": False,
                "reason": "inspected parent templates and pending human review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{len(cases)} candidates prepared; no approvals, same four task groups.")


if __name__ == "__main__":
    main()
