"""Evaluation validity checks: run with python -m unittest experiments.intent.test_pilot."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from .dataset import build_cases, validate
from .evaluate import ReferenceDetector, online_step, rate, run_case, summarize
from .evidence import verify


class EvaluationTests(unittest.TestCase):
    def test_group_leakage_rejected(self):
        cases = build_cases()
        cases[1]["split"] = "test"
        with self.assertRaisesRegex(ValueError, "leakage"):
            validate(cases)

    def test_unknown_label_rejected(self):
        cases = build_cases()
        cases[0]["steps"][0]["annotation"]["risk"] = None
        with self.assertRaises(ValueError):
            validate(cases)

    def test_no_oracle_or_future_features(self):
        case = next(c for c in build_cases() if c["family"] == "memory")
        step = case["steps"][2]
        before = online_step(step)
        step["annotation"] = {"risk": False, "reason": "changed gold"}
        step["future"] = {"detected": True}
        self.assertEqual(before, online_step(step))
        detector = ReferenceDetector("reference_full", 3)
        self.assertFalse(detector(case["intent"], before, []) ["detected"])
        self.assertTrue(detector(case["intent"], before, [before, before])["detected"])

    def test_zero_denominator_is_na(self):
        self.assertIsNone(rate(0, 0)["value"])
        self.assertIsNone(rate(0, 0)["wilson95"])

    def test_evidence_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "raw.jsonl").write_bytes(b"original")
            (root / "checksums.json").write_text(json.dumps({"raw.jsonl": hashlib.sha256(b"original").hexdigest()}))
            self.assertEqual(verify(root), 1)
            (root / "raw.jsonl").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                verify(root)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_legitimate_goal_change_is_usable(self):
        case = next(c for c in build_cases() if c["case_id"] == "normal-2-0")
        row = await run_case(case, "reference_full", 3)
        self.assertTrue(row["safe_task_success"])
        self.assertFalse(any(r["decision_detected"] for r in row["records"]))
        self.assertEqual(row["records"][-1]["contract_version"], 2)

    async def test_permission_block_precedes_actual_write(self):
        case = next(c for c in build_cases() if c["case_id"] == "hard_boundary-0-0")
        row = await run_case(case, "runtime_boundary", 3)
        self.assertEqual(row["records"][1]["status"], "blocked_before_effect")
        self.assertIsNone(row["records"][1]["after_digest"])
        self.assertFalse(row["records"][-1]["reached"])
        self.assertFalse(row["safe_task_success"])

    async def test_unsafe_write_not_hidden_by_later_safe_report(self):
        case = next(c for c in build_cases() if c["family"] == "retrieval")
        row = await run_case(case, "runtime_boundary", 3)
        self.assertTrue(row["unsafe_effect_observed"])
        self.assertFalse(row["safe_task_success"])
        self.assertEqual(row["records"][-1]["status"], "write_completed")

    async def test_safe_stop_not_task_completion(self):
        case = next(c for c in build_cases() if c["family"] == "retrieval")
        row = await run_case(case, "reference_no_correction", 3)
        self.assertFalse(row["unsafe_effect_observed"])
        self.assertFalse(row["safe_task_success"])
        self.assertFalse(row["records"][-1]["reached"])

    async def test_recoverable_tool_failure_kept(self):
        case = next(c for c in build_cases() if c["case_id"] == "normal-3-0")
        row = await run_case(case, "reference_full", 3)
        self.assertTrue(any(r["status"] == "tool_failed" and r["error"] for r in row["records"]))
        self.assertTrue(row["safe_task_success"])


if __name__ == "__main__":
    unittest.main()
