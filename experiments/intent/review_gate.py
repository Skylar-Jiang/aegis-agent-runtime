"""Validate genuinely filled review records and prepare a policy feedback gate.
This validates record completeness, not the identity or truth of human reviewers.
It never writes approval into empty review files or deploys a policy.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path


def validate(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    errors = []
    ids = set()
    for row in rows:
        cid = row["case_id"]
        if cid in ids:
            errors.append(f"{cid}: duplicate case")
        ids.add(cid)
        if row["status"] != "approved":
            errors.append(f"{cid}: not approved")
        a, b = row["reviewer_1"].strip(), row["reviewer_2"].strip()
        if not a or not b or a == b:
            errors.append(f"{cid}: two distinct reviewers required")
        if any(
            row[f"reviewer_{i}_verdict"] not in {"agree", "disagree"} for i in (1, 2)
        ):
            errors.append(f"{cid}: reviewer verdict missing")
        if (
            "disagree" in (row["reviewer_1_verdict"], row["reviewer_2_verdict"])
            or row["disagreement"]
        ) and not row["resolution"]:
            errors.append(f"{cid}: unresolved disagreement")
        if not row["evidence_ref"] or not row["reviewed_at"]:
            errors.append(f"{cid}: evidence/date missing")
    if not rows:
        errors.append("empty review file")
    return {
        "valid": not errors,
        "rows": len(rows),
        "errors": errors,
        "record_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "human_identity_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.review)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Review records complete: {result['valid']}; rows={result['rows']}")
    raise SystemExit(0 if result["valid"] else 2)


if __name__ == "__main__":
    main()
