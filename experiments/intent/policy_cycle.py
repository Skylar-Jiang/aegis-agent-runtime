"""Release/rollback an opt-in local Intent policy only after real human review.
The same preset controls remain enabled; only repeat_limit (2..5) is adjustable.
A local operator must supply authentic reviews and the matching real regression.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from .review_gate import validate
from .evidence import verify
from .dataset import load

ROOT = Path(__file__).resolve().parents[2]


def apply(review, feedback, run, runtime, approved_by):
    checked = validate(review)
    if not checked["valid"]:
        raise ValueError("Human review gate incomplete")
    with Path(feedback).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or any(
        not row["reviewer"]
        or not row["evaluation_ref"]
        or row["approval"] != "approved"
        or row["approved_by"] != approved_by
        for row in rows
    ):
        raise ValueError(
            "Approved feedback with evaluator and evidence references required"
        )
    verify(run)
    summary = json.loads((Path(run) / "summary.json").read_text(encoding="utf-8"))
    if summary.get("source_unchanged_during_run") is not True:
        raise ValueError("Frozen source validation is required before policy release")
    full = summary["arms"]["full"]
    threshold = summary.get("repeat_limit", 3)
    if (
        summary["failures"]
        or full["completed_runs"] != 40
        or full["normal_safe_success"] != 8
        or full["safe_stops"] != 24
        or full["forbidden_effect_trajectories"]
    ):
        raise ValueError("Real full-regression acceptance gate failed")
    if not 2 <= threshold <= 5:
        raise ValueError("Unsupported repeat threshold")
    with Path(review).open(encoding="utf-8-sig", newline="") as stream:
        reviewed = list(csv.DictReader(stream))
    if {r["case_id"] for r in reviewed} != {c["case_id"] for c in load()}:
        raise ValueError("All forty pilot cases require review before policy release")
    if any(r["case_id"] not in {c["case_id"] for c in load()} for r in rows):
        raise ValueError("Unknown feedback case")
    reviewers = sorted({r[k] for r in reviewed for k in ("reviewer_1", "reviewer_2")})
    runtime = Path(runtime).resolve()
    if (
        not runtime.is_relative_to(ROOT / ".runtime/intent-integration")
        or not (runtime / "state.sqlite3").exists()
    ):
        raise ValueError("An existing isolated Intent runtime is required")
    active = runtime / "intent-policy.json"
    old = active.read_bytes() if active.exists() else None
    previous = (
        {
            "schema_version": "intent-policy-release-v1",
            "version": "intent-policy:1",
            "repeat_limit": 3,
            "kind": "preset",
        }
        if old is None
        else json.loads(old)
    )
    archive = runtime / "policy-history"
    archive.mkdir(exist_ok=True)
    previous_bytes = json.dumps(previous, ensure_ascii=False, sort_keys=True).encode()
    previous_hash = hashlib.sha256(previous_bytes).hexdigest()
    (archive / (previous_hash + ".json")).write_bytes(previous_bytes)
    release = {
        "schema_version": "intent-policy-release-v1",
        "version": "intent-feedback:"
        + hashlib.sha256(Path(feedback).read_bytes()).hexdigest()[:12],
        "repeat_limit": threshold,
        "reviewers": reviewers,
        "approved_by": approved_by,
        "feedback_refs": [r["feedback_id"] for r in rows],
        "review_sha256": checked["record_sha256"],
        "feedback_sha256": hashlib.sha256(Path(feedback).read_bytes()).hexdigest(),
        "validation_ref": str(Path(run).resolve()),
        "validation_sha256": hashlib.sha256(
            (Path(run) / "summary.json").read_bytes()
        ).hexdigest(),
        "rollback_sha256": previous_hash,
        "claim": "record completeness checked; human identity and official validity require actual team confirmation",
    }
    staged = runtime / "intent-policy.pending"
    staged.write_text(
        json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(staged, active)
    return release


def rollback(runtime, approved_by):
    runtime = Path(runtime).resolve()
    if (
        not runtime.is_relative_to(ROOT / ".runtime/intent-integration")
        or not (runtime / "state.sqlite3").exists()
    ):
        raise ValueError("Existing isolated runtime required")
    active = runtime / "intent-policy.json"
    release = json.loads(active.read_text(encoding="utf-8"))
    digest = release["rollback_sha256"]
    old = (Path(runtime) / "policy-history" / (digest + ".json")).read_bytes()
    if hashlib.sha256(old).hexdigest() != digest:
        raise ValueError("Rollback snapshot corrupted")
    staged = Path(runtime) / "intent-policy.pending"
    staged.write_bytes(old)
    os.replace(staged, active)
    (Path(runtime) / "policy-rollback-receipt.json").write_text(
        json.dumps(
            {
                "approved_by": approved_by,
                "from": release["version"],
                "to": json.loads(old)["version"],
                "snapshot_sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--feedback", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        rollback(args.runtime, args.approved_by)
    else:
        if not all((args.review, args.feedback, args.run)):
            parser.error("review, feedback and run are required")
        print(
            json.dumps(
                apply(
                    args.review, args.feedback, args.run, args.runtime, args.approved_by
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
