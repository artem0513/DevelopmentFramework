"""Gate protocol fixtures are not independent semantic model validation."""
import unittest
from unittest.mock import patch

from ase.gates import evaluate_gate
from ase.state import hash_value


class GateRegressionTests(unittest.TestCase):
    def setUp(self):
        self.contract = {"profile": "document", "acceptance": [
            {"id": "AC1", "expected": "A confirmed result", "mandatory": True, "evidence_ids": ["manual"]}],
            "evidence": [{"id": "manual", "kind": "manual", "required": True}], "unknowns": []}
        self.current = {"contract_hash": hash_value(self.contract), "proposal_hash": "proposal", "plan_hash": "plan",
                        "fingerprint": "source", "check_hash": "check", "checks": self.contract["evidence"]}
        self.checks = {"kind": "checks", "fingerprint": "source", "contract_hash": self.current["contract_hash"],
                       "plan_hash": "plan", "results": [{"id": "manual", "kind": "manual", "confirmed": True,
                                                           "user_message_ref": "chat:user:manual-proof"}]}
        self.ac = {"role": "ac", "execution_id": "ac-process"}
        self.review = {"role": "review", "execution_id": "review-process"}

    def judgment(self, receipt, **kwargs):
        payload = ({"criteria": [{"id": "AC1", "expected": "A confirmed result", "status": "PASS",
                                   "evidence": ["manual confirmation"], "method": "compared", "limitations": [], "missing_coverage": []}]}
                   if receipt["role"] == "ac" else {"findings": [], "summary": "reviewed"})
        return {"valid": True, "evidence_kind": "independent_cli", "result": {"payload": payload}}

    def test_missing_independent_roles_block_approval(self):
        self.assertEqual(evaluate_gate(self.contract, self.current, [self.checks], [])["status"], "BLOCKED")

    def test_protocol_fixtures_cannot_supply_semantic_approval(self):
        def fixture(receipt, **kwargs):
            return {**self.judgment(receipt), "evidence_kind": "protocol_only"}
        with patch("ase.gates.verify_receipt", side_effect=fixture):
            self.assertEqual(evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])["status"], "BLOCKED")

    def test_changed_execution_plan_requires_fresh_receipts(self):
        self.checks["plan_hash"] = "old-plan"
        with patch("ase.gates.verify_receipt", side_effect=self.judgment):
            self.assertEqual(evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])["status"], "REJECTED")

    def test_p2_and_missing_mandatory_coverage_reject(self):
        def finding(receipt, **kwargs):
            result = self.judgment(receipt)
            if receipt["role"] == "review":
                result["result"]["payload"]["findings"] = [{"severity": "P2", "path": "result", "line": 1, "message": "Requirement missing"}]
            return result
        with patch("ase.gates.verify_receipt", side_effect=finding):
            self.assertEqual(evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])["status"], "REJECTED")
        self.contract["acceptance"][0]["evidence_ids"] = []
        self.current["contract_hash"] = hash_value(self.contract)
        self.checks["contract_hash"] = self.current["contract_hash"]
        with patch("ase.gates.verify_receipt", side_effect=self.judgment):
            self.assertEqual(evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])["status"], "BLOCKED")

    def test_unverified_assertion_never_overrides_actual_proof(self):
        self.checks["results"][0]["confirmed"] = False
        with patch("ase.gates.verify_receipt", side_effect=self.judgment):
            result = evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])
        self.assertEqual(result["status"], "BLOCKED")

    def test_extra_unverified_requirement_cannot_be_silently_ignored(self):
        def extra(receipt, **kwargs):
            value = self.judgment(receipt)
            if receipt["role"] == "ac":
                value["result"]["payload"]["criteria"].append({"id": "request_constraints", "status": "UNVERIFIED"})
            return value
        with patch("ase.gates.verify_receipt", side_effect=extra):
            result = evaluate_gate(self.contract, self.current, [self.checks, self.ac, self.review], [])
        self.assertEqual(result["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
