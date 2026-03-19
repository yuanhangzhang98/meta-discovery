#!/usr/bin/env python3
"""Validation for MCGS subagent outputs.

Provides detect-only validation for Planner, Designer, and Objective Agent outputs,
plus a protected-files check for Designer worktrees. Returns JSON with validation
results so the orchestrator can decide whether to SendMessage feedback or fallback.

Usage:
    python validate_agent_output.py validate-planner --file <path>
    python validate_agent_output.py validate-designer --file <path> --reference-nodes 3,7
    python validate_agent_output.py validate-objective --file <path> --sample-results '{"loss": 0.5}'
    python validate_agent_output.py check-protected --worktree <path> --parent-branch <ref> --protected "file1,pattern/*"
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


# ──────────────────────────────────────────────────────────────────────────────
# Planner validation
# ──────────────────────────────────────────────────────────────────────────────

VALID_PHASES = {
    "early_exploration",
    "systematic_search",
    "exploitation",
    "breakthrough_needed",
}


def validate_planner(data: dict) -> Dict[str, Any]:
    """Validate Planner agent JSON output.

    Checks:
        - research_direction: non-empty string
        - reference_node_ids: non-empty list of integers
        - focus_areas: list
        - avoid_areas: list
        - current_phase: one of VALID_PHASES
        - key_insights: list
    """
    errors: List[str] = []

    # research_direction
    rd = data.get("research_direction")
    if not rd or not isinstance(rd, str) or not rd.strip():
        errors.append("'research_direction' is missing or empty")

    # reference_node_ids
    ref_ids = data.get("reference_node_ids")
    if not ref_ids or not isinstance(ref_ids, list):
        errors.append("'reference_node_ids' is missing or not a list")
    elif not all(isinstance(x, int) for x in ref_ids):
        errors.append("'reference_node_ids' must contain only integers")
    elif len(ref_ids) == 0:
        errors.append("'reference_node_ids' is empty")

    # focus_areas
    fa = data.get("focus_areas")
    if fa is None:
        errors.append("'focus_areas' is missing")
    elif not isinstance(fa, list):
        errors.append("'focus_areas' must be a list")

    # avoid_areas
    aa = data.get("avoid_areas")
    if aa is None:
        errors.append("'avoid_areas' is missing")
    elif not isinstance(aa, list):
        errors.append("'avoid_areas' must be a list")

    # current_phase
    phase = data.get("current_phase")
    if not phase:
        errors.append("'current_phase' is missing")
    elif phase not in VALID_PHASES:
        errors.append(
            f"'current_phase' is '{phase}', must be one of: {', '.join(sorted(VALID_PHASES))}"
        )

    # key_insights
    ki = data.get("key_insights")
    if ki is None:
        errors.append("'key_insights' is missing")
    elif not isinstance(ki, list):
        errors.append("'key_insights' must be a list")

    return {"valid": len(errors) == 0, "errors": errors}


# ──────────────────────────────────────────────────────────────────────────────
# Designer validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_designer(data: dict, reference_node_ids: List[int]) -> Dict[str, Any]:
    """Validate Designer agent mcgs_design_output.json.

    Checks:
        - short_name: exists, ≤40 chars
        - description: exists, non-empty
        - reference_weights: array-of-objects [{node_id: int, weight: float}],
          covers all reference_node_ids, weights sum to 1.0 (±0.01)

    Missing reference nodes are auto-fixed with weight 0 (warning, not error).
    """
    errors: List[str] = []
    warnings: List[str] = []

    # short_name
    sn = data.get("short_name")
    if not sn or not isinstance(sn, str) or not sn.strip():
        errors.append("'short_name' is missing or empty")
    elif len(sn) > 40:
        errors.append(f"'short_name' is {len(sn)} chars, must be ≤40")

    # description
    desc = data.get("description")
    if not desc or not isinstance(desc, str) or not desc.strip():
        errors.append("'description' is missing or empty")

    # reference_weights
    rw = data.get("reference_weights")
    if rw is None:
        errors.append("'reference_weights' is missing")
    elif isinstance(rw, dict):
        errors.append(
            "'reference_weights' is a dict — must be array-of-objects: "
            '[{"node_id": <int>, "weight": <float>}, ...]'
        )
    elif isinstance(rw, list):
        # Check each entry
        found_ids = set()
        total_weight = 0.0
        for i, entry in enumerate(rw):
            if not isinstance(entry, dict):
                errors.append(f"reference_weights[{i}] is not an object")
                continue
            nid = entry.get("node_id")
            w = entry.get("weight")
            if nid is None or not isinstance(nid, (int, float)):
                errors.append(f"reference_weights[{i}] missing or invalid 'node_id'")
            else:
                found_ids.add(int(nid))
            if w is None or not isinstance(w, (int, float)):
                errors.append(f"reference_weights[{i}] missing or invalid 'weight'")
            elif not (0.0 <= float(w) <= 1.0):
                errors.append(
                    f"reference_weights[{i}] weight={w} out of range [0.0, 1.0]"
                )
            else:
                total_weight += float(w)

        # Check all reference nodes are covered — auto-fix missing ones
        expected = set(reference_node_ids)
        missing = expected - found_ids
        if missing:
            # Auto-fix: add missing nodes with weight 0 (designer didn't use them)
            for mid in sorted(missing):
                rw.append({"node_id": mid, "weight": 0.0})
            warnings.append(
                f"Auto-fixed: added missing reference nodes {sorted(missing)} with weight 0"
            )

        # Check sum
        if rw and abs(total_weight - 1.0) > 0.01:
            errors.append(
                f"reference_weights sum to {total_weight:.3f}, expected 1.0 (±0.01)"
            )
    else:
        errors.append("'reference_weights' must be an array of objects")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "data": data}


# ──────────────────────────────────────────────────────────────────────────────
# Objective Agent validation
# ──────────────────────────────────────────────────────────────────────────────

FORBIDDEN_IMPORTS = {
    "subprocess", "os.system", "os.popen", "requests", "urllib",
    "http", "socket", "shutil", "pathlib", "tempfile",
}


def validate_objective(
    file_path: str,
    sample_results: dict,
    metadata: dict | None = None,
) -> Dict[str, Any]:
    """Validate an Objective Agent's Python file and optional metadata.

    Checks:
        - File compiles
        - Contains a function named 'objective' with one parameter
        - No forbidden imports
        - Calling objective(sample_results) returns a finite float
        - Metadata (if provided) has 'name' and 'description'
    """
    errors: List[str] = []
    path = Path(file_path)

    # Check file exists
    if not path.exists():
        return {"valid": False, "errors": [f"File not found: {file_path}"]}

    source = path.read_text(encoding="utf-8")

    # Compile check
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return {"valid": False, "errors": [f"Syntax error: {e}"]}

    # Check for 'objective' function
    func_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "objective":
            func_found = True
            # Check it has exactly one parameter (excluding self)
            params = node.args
            n_args = len(params.args)
            if n_args != 1:
                errors.append(
                    f"'objective' function has {n_args} parameter(s), expected 1"
                )
            break
    if not func_found:
        errors.append("No function named 'objective' found")

    # Check for forbidden imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in FORBIDDEN_IMPORTS:
                    if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                        errors.append(f"Forbidden import: '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in FORBIDDEN_IMPORTS:
                    if node.module == forbidden or node.module.startswith(forbidden + "."):
                        errors.append(f"Forbidden import: 'from {node.module}'")

    # If we found critical errors, skip runtime test
    if errors:
        return {"valid": False, "errors": errors}

    # Runtime test: call objective(sample_results)
    try:
        namespace: Dict[str, Any] = {}
        exec(compile(source, file_path, "exec"), namespace)  # noqa: S102
        obj_fn = namespace.get("objective")
        if obj_fn is None:
            errors.append("'objective' function not found at runtime")
        else:
            result = obj_fn(sample_results)
            if not isinstance(result, (int, float)):
                errors.append(
                    f"objective() returned {type(result).__name__}, expected float"
                )
            elif not math.isfinite(result):
                errors.append(f"objective() returned non-finite value: {result}")
    except Exception as e:
        errors.append(f"objective() raised {type(e).__name__}: {e}")

    # Metadata validation
    if metadata is not None:
        name = metadata.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            errors.append("metadata 'name' is missing or empty")
        desc = metadata.get("description")
        if not desc or not isinstance(desc, str) or not desc.strip():
            errors.append("metadata 'description' is missing or empty")

    return {"valid": len(errors) == 0, "errors": errors}


# ──────────────────────────────────────────────────────────────────────────────
# Protected files check
# ──────────────────────────────────────────────────────────────────────────────

def check_protected_files(
    worktree_path: str,
    parent_branch: str,
    protected_patterns: List[str],
) -> Dict[str, Any]:
    """Check if any protected files were modified in a worktree.

    Uses git diff --name-only against the parent branch, then matches
    changed files against protected patterns using fnmatch.
    """
    # Get list of changed files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", parent_branch],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {
                "violations": [],
                "all_changed": [],
                "error": f"git diff failed: {result.stderr.strip()}",
            }
    except FileNotFoundError:
        return {
            "violations": [],
            "all_changed": [],
            "error": "git not found",
        }

    changed = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

    # Also check untracked files (new files the Designer created)
    result_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if result_untracked.returncode == 0:
        untracked = [f.strip() for f in result_untracked.stdout.strip().split("\n") if f.strip()]
        # Exclude mcgs_design_output.json from untracked check — it's expected
        untracked = [f for f in untracked if f != "mcgs_design_output.json"]
        changed.extend(untracked)

    # Match against protected patterns
    violations = []
    for filepath in changed:
        for pattern in protected_patterns:
            if fnmatch.fnmatch(filepath, pattern) or filepath == pattern:
                violations.append(filepath)
                break

    return {"violations": violations, "all_changed": changed}


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _load_json_file(path: str) -> dict:
    """Load and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCGS subagent output validation")
    sub = parser.add_subparsers(dest="action", required=True)

    # validate-planner
    p_plan = sub.add_parser("validate-planner", help="Validate Planner output")
    p_plan.add_argument("--file", required=True, help="Path to planner output JSON")

    # validate-designer
    p_des = sub.add_parser("validate-designer", help="Validate Designer output")
    p_des.add_argument("--file", required=True, help="Path to mcgs_design_output.json")
    p_des.add_argument(
        "--reference-nodes", required=True,
        help="Comma-separated reference node IDs (e.g., 3,7)",
    )

    # validate-objective
    p_obj = sub.add_parser("validate-objective", help="Validate Objective Agent output")
    p_obj.add_argument("--file", required=True, help="Path to objective .py file")
    p_obj.add_argument(
        "--sample-results", required=True,
        help="JSON string of sample experiment results",
    )
    p_obj.add_argument(
        "--metadata", default=None,
        help="JSON string of objective metadata (optional)",
    )

    # check-protected
    p_prot = sub.add_parser("check-protected", help="Check for protected file violations")
    p_prot.add_argument("--worktree", required=True, help="Path to Designer worktree")
    p_prot.add_argument("--parent-branch", required=True, help="Parent branch ref")
    p_prot.add_argument(
        "--protected", required=True,
        help="Comma-separated protected file patterns (e.g., 'run_experiment.py,tests/*')",
    )

    args = parser.parse_args()

    if args.action == "validate-planner":
        try:
            data = _load_json_file(args.file)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            result = {"valid": False, "errors": [f"Cannot load file: {e}"]}
            print(json.dumps(result, indent=2))
            sys.exit(1)
        result = validate_planner(data)

    elif args.action == "validate-designer":
        try:
            data = _load_json_file(args.file)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            result = {"valid": False, "errors": [f"Cannot load file: {e}"]}
            print(json.dumps(result, indent=2))
            sys.exit(1)
        ref_nodes = [int(x.strip()) for x in args.reference_nodes.split(",")]
        result = validate_designer(data, ref_nodes)

    elif args.action == "validate-objective":
        sample = json.loads(args.sample_results)
        metadata = json.loads(args.metadata) if args.metadata else None
        result = validate_objective(args.file, sample, metadata)

    elif args.action == "check-protected":
        patterns = [p.strip() for p in args.protected.split(",")]
        result = check_protected_files(args.worktree, args.parent_branch, patterns)

    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2))
    if not result.get("valid", True) or result.get("violations"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
