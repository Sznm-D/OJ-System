import json
from pathlib import Path

import pytest

from backend.models import Problem


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_IDS = ["P1001", "P1002", "P1003"]


@pytest.mark.parametrize("problem_id", EXAMPLE_IDS)
def test_example_problem_is_complete_and_has_eight_testcases(problem_id):
    path = ROOT / "examples" / f"{problem_id}.json"
    assert path.exists(), f"missing example problem: {problem_id}"
    problem = Problem.model_validate_json(path.read_text(encoding="utf-8"))
    assert problem.id == problem_id
    assert len(problem.testcases) == 8
    assert problem.samples
    assert all(case.input is not None and case.output is not None for case in problem.testcases)


@pytest.mark.parametrize("problem_id", EXAMPLE_IDS)
def test_example_problem_disk_copy_matches_source(problem_id):
    source = json.loads((ROOT / "examples" / f"{problem_id}.json").read_text(encoding="utf-8"))
    installed = json.loads((ROOT / "data" / "problems" / f"{problem_id}.json").read_text(encoding="utf-8"))
    assert installed["id"] == source["id"] == problem_id
    assert len(installed["testcases"]) == 8
