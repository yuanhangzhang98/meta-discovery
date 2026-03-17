#!/usr/bin/env python3
"""Scripted post-designer iteration pipeline for MCGS.

Handles the deterministic steps after the Designer finishes:
  1. Validate mcgs_design_output.json
  2. Check for protected file modifications
  3. Commit and register node (if validation passes)
  4. Remove designer worktree
  5. Execute experiment
  6. Score objectives (multi-objective)
  7. Compute consensus (multi-objective)
  8. Update UCB scores

If validation fails (steps 1-2), exits with a JSON error report WITHOUT
committing. The orchestrator can then SendMessage to the Designer for
a retry, then re-call this script with --commit.

Usage:
    # Validate only (no commit/execute):
    python run_iteration.py validate \
        --graph mcgs_graph.json \
        --worktree /tmp/mcgs-worktree-5 \
        --reference-nodes "3,7" \
        --protected "run_experiment.py,mcgs_graph.json"

    # Full pipeline (validate + commit + execute + score + UCB):
    python run_iteration.py run \
        --graph mcgs_graph.json \
        --repo-dir . \
        --worktree /tmp/mcgs-worktree-5 \
        --short-name "sigmoid_gate" \
        --description "Added sigmoid gating" \
        --parent-edges '[{"node_id": 3, "weight": 0.7}, {"node_id": 7, "weight": 0.3}]' \
        --parent-branch mcgs/node-3 \
        --protected "run_experiment.py,mcgs_graph.json" \
        --reference-nodes "3,7" \
        --timeout 300
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import _run_git, load_graph, save_graph, cleanup_stale_worktrees


def _run_script(script_name: str, args: List[str], cwd: str | Path = ".") -> Dict[str, Any]:
    """Run a Python script from the scripts directory and return parsed output."""
    scripts_dir = Path(__file__).parent
    cmd = [sys.executable, str(scripts_dir / script_name)] + args

    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60)

    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def validate_step(
    worktree: str,
    reference_nodes: str,
    protected: str,
    parent_branch: str,
) -> Dict[str, Any]:
    """Run validation checks on the designer's output.

    Returns dict with 'valid', 'design_errors', 'protected_violations'.
    """
    design_file = Path(worktree) / "mcgs_design_output.json"
    result: Dict[str, Any] = {
        "valid": True,
        "design_errors": [],
        "protected_violations": [],
    }

    # Check design output
    if design_file.exists():
        validate_result = _run_script("validate_agent_output.py", [
            "validate-designer",
            "--file", str(design_file),
            "--reference-nodes", reference_nodes,
        ])
        try:
            parsed = json.loads(validate_result["stdout"])
            if not parsed.get("valid", True):
                result["design_errors"] = parsed.get("errors", [])
                result["valid"] = False
        except json.JSONDecodeError:
            result["design_errors"] = [f"Validation script error: {validate_result['stderr']}"]
            result["valid"] = False
    else:
        result["design_errors"] = ["mcgs_design_output.json not found in worktree"]
        result["valid"] = False

    # Check protected files
    if protected:
        protect_result = _run_script("validate_agent_output.py", [
            "check-protected",
            "--worktree", worktree,
            "--parent-branch", parent_branch,
            "--protected", protected,
        ])
        try:
            parsed = json.loads(protect_result["stdout"])
            violations = parsed.get("violations", [])
            if violations:
                result["protected_violations"] = violations
                result["valid"] = False
        except json.JSONDecodeError:
            pass  # Non-critical

    return result


def commit_step(
    worktree: str,
    graph_path: str,
    short_name: str,
    description: str,
    parent_edges: str,
    repo_dir: str,
) -> Dict[str, Any]:
    """Create branch, commit, register node, and clean up worktree.

    Returns dict with 'node_id' and 'branch'.
    """
    graph = load_graph(graph_path)
    new_id = graph.next_id
    new_branch = f"mcgs/node-{new_id}"

    # Create branch and commit in worktree
    _run_git(["checkout", "-b", new_branch], cwd=worktree)
    _run_git(["add", "-A"], cwd=worktree)

    # Check if there are changes
    status = _run_git(["status", "--porcelain"], cwd=worktree)
    if status.stdout.strip():
        _run_git(["commit", "-m", f"MCGS node {new_id}: {short_name}"], cwd=worktree)

    # Remove worktree BEFORE execute to avoid "already checked out" errors
    _run_git(["worktree", "remove", worktree, "--force"], cwd=repo_dir, check=False)

    # Register node
    register_result = _run_script("register_node.py", [
        "--graph", graph_path,
        "--short-name", short_name,
        "--description", description,
        "--branch", new_branch,
        "--parent-edges", parent_edges,
    ], cwd=repo_dir)

    try:
        reg_data = json.loads(register_result["stdout"])
        return {"node_id": reg_data["node_id"], "branch": new_branch}
    except (json.JSONDecodeError, KeyError):
        return {"node_id": new_id, "branch": new_branch, "warning": register_result["stderr"]}


def execute_step(
    graph_path: str,
    node_id: int,
    repo_dir: str,
    timeout: int,
) -> Dict[str, Any]:
    """Execute experiment, score objectives, compute consensus, update UCB."""
    graph = load_graph(graph_path)
    results: Dict[str, Any] = {}

    # Execute experiment
    exec_result = _run_script("execute_node.py", [
        "--node-id", str(node_id),
        "--graph", graph_path,
        "--repo-dir", repo_dir,
        "--timeout", str(timeout),
    ], cwd=repo_dir)
    results["execute"] = {
        "returncode": exec_result["returncode"],
        "output": exec_result["stdout"][-500:],
    }

    # Reload graph to check status
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None or node.status == "failed":
        results["status"] = "failed"
        results["stderr"] = node.stderr if node else exec_result["stderr"][-500:]
        return results

    # Multi-objective: score + consensus
    if graph.config.multi_objective:
        # Score objectives
        score_result = _run_script("run_objectives.py", [
            "--node-id", str(node_id),
            "--graph", graph_path,
        ], cwd=repo_dir)
        results["score_objectives"] = score_result["stdout"][-300:]

        # Compute consensus
        consensus_result = _run_script("consensus.py", [
            "--graph", graph_path,
        ], cwd=repo_dir)
        results["consensus"] = consensus_result["stdout"][-300:]

    # Update UCB scores
    ucb_result = _run_script("compute_ucb.py", [
        "--graph", graph_path,
    ], cwd=repo_dir)
    results["ucb"] = ucb_result["stdout"][-200:]

    # Get final node state
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node:
        results["status"] = node.status
        results["objective"] = node.objective
        results["consensus_score"] = node.consensus_score
        results["ucb_score"] = node.ucb_score

    return results


def run_full_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    """Run the complete post-designer pipeline."""
    output: Dict[str, Any] = {"phase": "run"}

    # Step 1-2: Validate
    validation = validate_step(
        worktree=args.worktree,
        reference_nodes=args.reference_nodes,
        protected=args.protected or "",
        parent_branch=args.parent_branch,
    )
    output["validation"] = validation

    if not validation["valid"]:
        output["action_needed"] = "fix_and_retry"
        output["message"] = "Validation failed. Fix errors and re-run."
        print(json.dumps(output, indent=2))
        return output

    # Read design output for node metadata
    design_file = Path(args.worktree) / "mcgs_design_output.json"
    if design_file.exists():
        design_data = json.loads(design_file.read_text(encoding="utf-8"))
        short_name = args.short_name or design_data.get("short_name", "unnamed")
        description = args.description or design_data.get("description", "")
    else:
        short_name = args.short_name or "unnamed"
        description = args.description or ""

    # Step 3-4: Commit and register
    commit = commit_step(
        worktree=args.worktree,
        graph_path=args.graph,
        short_name=short_name,
        description=description,
        parent_edges=args.parent_edges,
        repo_dir=args.repo_dir,
    )
    output["commit"] = commit

    # Safety: clean up any lingering worktrees
    cleanup_stale_worktrees(args.repo_dir)

    # Step 5-8: Execute + score + consensus + UCB
    execution = execute_step(
        graph_path=args.graph,
        node_id=commit["node_id"],
        repo_dir=args.repo_dir,
        timeout=args.timeout,
    )
    output["execution"] = execution

    print(json.dumps(output, indent=2, default=str))
    return output


def run_validate_only(args: argparse.Namespace) -> Dict[str, Any]:
    """Run validation only (no commit/execute)."""
    validation = validate_step(
        worktree=args.worktree,
        reference_nodes=args.reference_nodes,
        protected=args.protected or "",
        parent_branch=args.parent_branch,
    )
    output = {"phase": "validate", "validation": validation}
    print(json.dumps(output, indent=2))
    return output


def main():
    parser = argparse.ArgumentParser(description="MCGS post-designer iteration pipeline")
    sub = parser.add_subparsers(dest="action", required=True)

    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--graph", default="mcgs_graph.json")
    common.add_argument("--worktree", required=True, help="Path to designer worktree")
    common.add_argument("--reference-nodes", required=True, help="Comma-separated reference node IDs")
    common.add_argument("--protected", default="", help="Comma-separated protected file patterns")
    common.add_argument("--parent-branch", required=True, help="Parent branch ref")

    # Validate only
    sub.add_parser("validate", parents=[common], help="Validate designer output only")

    # Full pipeline
    p_run = sub.add_parser("run", parents=[common], help="Full pipeline: validate + commit + execute")
    p_run.add_argument("--repo-dir", default=".", help="Path to git repository")
    p_run.add_argument("--short-name", default="", help="Override short_name from design output")
    p_run.add_argument("--description", default="", help="Override description")
    p_run.add_argument("--parent-edges", required=True,
                       help='JSON array: [{"node_id": 3, "weight": 0.7}, ...]')
    p_run.add_argument("--timeout", type=int, default=300, help="Experiment timeout (seconds)")

    args = parser.parse_args()

    if args.action == "validate":
        result = run_validate_only(args)
        sys.exit(0 if result["validation"]["valid"] else 1)
    elif args.action == "run":
        result = run_full_pipeline(args)
        status = result.get("execution", {}).get("status", "unknown")
        sys.exit(0 if status == "evaluated" else 1)


if __name__ == "__main__":
    main()
