from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_registry.dashboard import build_dashboard
from project_registry.cli import main
from project_registry.model import new_project
from project_registry.registry import merge_registry
from project_registry.understanding import scaffold_understanding
from project_registry.validation import validate_registry, validate_understanding


def repository(name: str = "demo") -> dict:
    return {
        "name": name,
        "name_with_owner": f"kmadhok/{name}",
        "url": f"https://github.com/kmadhok/{name}",
        "is_fork": False,
        "is_private": True,
        "is_archived": False,
        "visibility": "private",
        "pushed_at": "2026-07-18T00:00:00Z",
    }


def snapshot() -> dict:
    return {
        "schema_version": 1,
        "owner": "kmadhok",
        "generated_at": "2026-07-18T00:00:00Z",
        "repositories": [repository()],
        "open_pull_requests": [],
        "open_issues": [],
        "errors": [],
    }


def brief() -> dict:
    return {
        "schema_version": 1,
        "repository": "kmadhok/demo",
        "analyzed_at": "2026-07-18T00:00:00Z",
        "revision": "1234567890abcdef",
        "analysis_scope": {
            "inspected": ["README.md"],
            "missing": [],
            "inaccessible": [],
            "sampling_notes": None,
        },
        "stated_purpose": "Demonstrate the registry.",
        "observed_capabilities": [
            {
                "statement": "Contains a documented demo.",
                "claim_type": "observed_fact",
                "evidence_ids": ["readme"],
            }
        ],
        "planned_work": [],
        "architecture": [],
        "maturity": "prototype",
        "accomplishments": [],
        "gaps": [],
        "risks": [],
        "suggested_next_actions": [],
        "questions": [],
        "evidence": [
            {
                "id": "readme",
                "kind": "documentation",
                "location": "README.md",
                "summary": "Describes the demo.",
            }
        ],
        "confidence": {"level": "medium", "rationale": "Documentation was inspected."},
        "limitations": ["The demo was not executed."],
    }


class RegistryTests(unittest.TestCase):
    def test_new_project_does_not_guess_purpose(self) -> None:
        project = new_project(repository())
        self.assertEqual(project["review_status"], "needs_review")
        self.assertIsNone(project["purpose"])
        self.assertFalse(project["active"])

    def test_fork_is_distinguished_as_reference(self) -> None:
        item = repository("forked")
        item["is_fork"] = True
        project = new_project(item)
        self.assertEqual(project["lifecycle"], "reference")
        self.assertEqual(project["review_status"], "needs_review")

    def test_merge_preserves_curated_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            curated = new_project(repository())
            curated.update(
                {
                    "review_status": "reviewed",
                    "purpose": "Human-owned purpose",
                    "lifecycle": "now",
                    "active": True,
                    "desired_outcome": "Finish the demo",
                    "next_action": "Run the demo",
                    "last_reviewed_at": "2026-07-18T00:00:00Z",
                }
            )
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "owner": "kmadhok",
                        "updated_at": "2026-07-18T00:00:00Z",
                        "projects": [curated],
                    }
                ),
                encoding="utf-8",
            )
            merged = merge_registry("kmadhok", snapshot(), path)
            self.assertEqual(merged["projects"][0]["purpose"], "Human-owned purpose")
            self.assertEqual(merged["projects"][0]["next_action"], "Run the demo")

    def test_active_project_requires_next_action(self) -> None:
        project = new_project(repository())
        project.update(
            {
                "review_status": "reviewed",
                "purpose": "Demo",
                "lifecycle": "now",
                "active": True,
                "desired_outcome": "Done",
                "last_reviewed_at": "2026-07-18T00:00:00Z",
            }
        )
        registry = {
            "schema_version": 1,
            "owner": "kmadhok",
            "updated_at": "2026-07-18T00:00:00Z",
            "projects": [project],
        }
        errors = validate_registry(registry, snapshot(), Path("."))
        self.assertTrue(any("requires one next_action" in error for error in errors))

    def test_understanding_rejects_unknown_evidence(self) -> None:
        value = brief()
        value["observed_capabilities"][0]["evidence_ids"] = ["missing"]
        errors = validate_understanding(value)
        self.assertTrue(any("unknown evidence id" in error for error in errors))

    def test_understanding_rejects_unstructured_scope(self) -> None:
        value = brief()
        value["analysis_scope"]["inspected"] = "README.md"
        errors = validate_understanding(value)
        self.assertTrue(any("analysis_scope.inspected: must be an array" in error for error in errors))

    def test_set_project_does_not_write_invalid_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            project = new_project(repository())
            projects_path = data / "projects.json"
            projects_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "owner": "kmadhok",
                        "updated_at": "2026-07-18T00:00:00Z",
                        "projects": [project],
                    }
                ),
                encoding="utf-8",
            )
            (data / "github_snapshot.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            original = projects_path.read_text(encoding="utf-8")
            result = main(
                [
                    "--root", str(root), "set-project", "kmadhok/demo",
                    "--review-status", "reviewed", "--lifecycle", "now", "--active",
                ]
            )
            self.assertEqual(result, 1)
            self.assertEqual(projects_path.read_text(encoding="utf-8"), original)

    def test_dashboard_contains_operational_sections(self) -> None:
        project = new_project(repository())
        registry = {
            "schema_version": 1,
            "owner": "kmadhok",
            "updated_at": "2026-07-18T00:00:00Z",
            "projects": [project],
        }
        output = build_dashboard(registry, snapshot())
        self.assertIn("## Portfolio summary", output)
        self.assertIn("## Open pull requests", output)
        self.assertIn("## Attention queue", output)
        self.assertIn("kmadhok/demo", output)

    def test_scaffold_ignores_dependency_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            dependency = root / "node_modules" / "package" / "README.md"
            dependency.parent.mkdir(parents=True)
            dependency.write_text("ignored", encoding="utf-8")
            value = scaffold_understanding(root, "kmadhok/demo")
            self.assertIn("README.md", value["analysis_scope"]["inspected"])
            self.assertNotIn(
                "node_modules/package/README.md", value["analysis_scope"]["inspected"]
            )


if __name__ == "__main__":
    unittest.main()
