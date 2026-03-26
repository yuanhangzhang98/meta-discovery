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

Uses direct function imports instead of subprocesses for efficiency —
the graph is loaded once, passed through the pipeline, and saved once.

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
        --parent-branch mcgs/node-3 \
        --protected "run_experiment.py,mcgs_graph.json" \
        --reference-nodes "3,7" \
        --timeout 300
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import (
    MCGSGraph,
    ParentEdge,
    run_git,
    load_graph,
    save_graph,
    cleanup_stale_worktrees,
)
from validate_agent_output import validate_designer, validate_planner, check_protected_files
from execute_node import execute_node
from run_objectives import run_objectives_on_node
from consensus import update_graph_with_consensus
from compute_ucb import update_all_scores


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
        try:
            data = json.loads(design_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            result["design_errors"] = [f"Cannot read design file: {e}"]
            result["valid"] = False
            return result

        ref_nodes = [int(x.strip()) for x in reference_nodes.split(",") if x.strip()]
        validation = validate_designer(data, ref_nodes)
        if not validation.get("valid", True):
            result["design_errors"] = validation.get("errors", [])
            result["valid"] = False
    else:
        result["design_errors"] = ["mcgs_design_output.json not found in worktree"]
        result["valid"] = False

    # Check protected files
    if protected:
        patterns = [p.strip() for p in protected.split(",") if p.strip()]
        protect_result = check_protected_files(worktree, parent_branch, patterns)
        violations = protect_result.get("violations", [])
        if violations:
            result["protected_violations"] = violations
            result["valid"] = False

    return result


def _read_design_output(worktree: str) -> Dict[str, Any]:
    """Read mcgs_design_output.json from worktree.

    Returns the parsed design data including short_name, description,
    and reference_weights (parent_edges).
    """
    design_file = Path(worktree) / "mcgs_design_output.json"
    if not design_file.exists():
        return {}
    return json.loads(design_file.read_text(encoding="utf-8"))


def commit_step(
    worktree: str,
    graph: MCGSGraph,
    graph_path: str,
    short_name: str,
    description: str,
    parent_edges: List[ParentEdge],
    repo_dir: str,
) -> Dict[str, Any]:
    """Register node, create branch, commit, and clean up worktree.

    Registers the node in the graph FIRST so it persists even if git
    operations fail. On git failure the node stays with status "failed".
    Returns dict with 'node_id' and 'branch'.
    """
    new_id = graph.next_id
    new_branch = MCGSGraph.node_branch_name(new_id)

    # Register node in graph FIRST — survives git failures
    node = graph.add_node(
        short_name=short_name,
        branch=new_branch,
        parent_edges=parent_edges,
        description=description,
    )
    save_graph(graph, graph_path)

    try:
        # Create branch and commit in worktree
        run_git(["checkout", "-b", new_branch], cwd=worktree)
        run_git(["add", "-A"], cwd=worktree)

        # Check if there are changes
        status = run_git(["status", "--porcelain"], cwd=worktree)
        if status.stdout.strip():
            run_git(["commit", "-m", f"MCGS node {new_id}: {short_name}"], cwd=worktree)

        # Remove worktree BEFORE execute to avoid "already checked out" errors
        run_git(["worktree", "remove", worktree, "--force"], cwd=repo_dir, check=False)

    except Exception as e:
        # Git failed — mark node as failed but keep it registered
        node.status = "failed"
        node.stderr = f"Git commit failed: {e}"
        save_graph(graph, graph_path)
        # Try to clean up worktree even on failure
        run_git(["worktree", "remove", worktree, "--force"], cwd=repo_dir, check=False)
        raise

    return {"node_id": node.id, "branch": new_branch}


def execute_step(
    graph_path: str,
    node_id: int,
    repo_dir: str,
    timeout: int,
) -> Dict[str, Any]:
    """Execute experiment, score objectives, compute consensus, update UCB.

    Uses direct function calls instead of subprocesses — loads graph once
    at end for final state.
    """
    results: Dict[str, Any] = {}

    # Execute experiment (this loads/saves graph internally)
    obj_result = execute_node(
        graph_path=graph_path,
        node_id=node_id,
        repo_dir=repo_dir,
        timeout=timeout,
    )
    results["execute"] = {"objective": obj_result}

    # Reload graph to check status
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None or node.status == "failed":
        results["status"] = "failed"
        results["stderr"] = node.stderr if node else "Node not found after execution"
        return results

    # Multi-objective: score + consensus (in-memory, single save)
    if graph.config.multi_objective:
        objectives_dir = Path(graph.config.objectives_dir)

        # Score objectives on this node
        scores = run_objectives_on_node(graph_path, node_id)
        results["score_objectives"] = scores

        # Compute consensus and update graph
        stats = update_graph_with_consensus(graph, objectives_dir)
        save_graph(graph, graph_path)
        results["consensus"] = {
            "num_objectives": stats.get("num_objectives", 0),
            "weights": stats.get("weights", {}),
        }

    # Update UCB scores (in-memory, single save)
    graph = load_graph(graph_path)
    update_all_scores(graph)
    save_graph(graph, graph_path)
    results["ucb"] = "updated"

    # Get final node state
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

    # Read design output for node metadata and parent_edges
    design_data = _read_design_output(args.worktree)
    short_name = args.short_name or design_data.get("short_name", "unnamed")
    description = args.description or design_data.get("description", "")

    # Determine parent_edges: from --parent-edges flag, or auto-read from design output
    if args.parent_edges and args.parent_edges != "{parent_edges}":
        raw_edges = json.loads(args.parent_edges)
    else:
        # Auto-read from mcgs_design_output.json (the key automation)
        raw_edges = design_data.get("reference_weights", [])
        if not raw_edges:
            raw_edges = [{"node_id": 0, "weight": 1.0}]

    parent_edges = [
        ParentEdge(node_id=int(e["node_id"]), weight=float(e["weight"]))
        for e in raw_edges
    ]

    # Step 3-4: Commit and register
    graph = load_graph(args.graph)
    commit = commit_step(
        worktree=args.worktree,
        graph=graph,
        graph_path=args.graph,
        short_name=short_name,
        description=description,
        parent_edges=parent_edges,
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
    p_run.add_argument("--parent-edges", default="",
                       help='JSON array: [{"node_id": 3, "weight": 0.7}, ...] (auto-read from design output if omitted)')
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
