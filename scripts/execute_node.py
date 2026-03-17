#!/usr/bin/env python3
"""Execution engine: run the experiment/objective script on a node's code.

This script is deterministic (no LLM involved). It:
  1. Creates a temporary git worktree for the node's branch
  2. Runs the user's script in that worktree
  3. Parses output: JSON experiment results (multi-objective) or float (single-objective)
  4. Updates the node's state in mcgs_graph.json
  5. Cleans up the worktree

In multi-objective mode (experiment_script configured), the script should output
a JSON object as its last stdout line containing experiment metrics. These are
stored in node.experiment_results for objective functions to score later.

In single-objective mode (default), the script outputs a float on its last line.

Usage:
    python execute_node.py --node-id 5 --graph mcgs_graph.json [--timeout 300] [--repo-dir .]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import load_graph, save_graph, _run_git


def execute_node(
    graph_path: str,
    node_id: int,
    repo_dir: str = ".",
    timeout: int = 300,
    fidelity: str | None = None,
) -> float | None:
    """Execute the objective function for a specific node.

    Args:
        graph_path: Path to mcgs_graph.json
        node_id: ID of the node to evaluate
        repo_dir: Path to the git repository
        timeout: Maximum seconds to run the objective script
        fidelity: Optional fidelity tier name (e.g., "low", "medium", "high").
            If set, uses the tier's timeout and sets env vars from the tier config.
            Results are stored under node.fidelity_results[tier_name].

    Returns:
        The objective value if successful, None if failed.
    """
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None:
        print(f"Error: Node {node_id} not found in graph", file=sys.stderr)
        return None

    if node.status == "evaluated" and node.objective is not None and fidelity is None:
        print(f"Node {node_id} already evaluated (objective={node.objective:.4f})")
        return node.objective

    # Resolve fidelity tier config
    fidelity_env: dict[str, str] = {}
    if fidelity and graph.config.multi_fidelity:
        for tier in graph.config.fidelity_tiers:
            if tier["name"] == fidelity:
                timeout = tier.get("timeout", timeout)
                fidelity_env = tier.get("env", {})
                break
        else:
            print(f"Warning: Fidelity tier '{fidelity}' not found in config", file=sys.stderr)

    # Choose which script to run: experiment_script (multi-objective) or objective_script (single)
    multi_objective = graph.config.multi_objective
    script_name = graph.config.experiment_script if multi_objective else graph.config.objective_script
    branch = node.branch
    repo_dir = Path(repo_dir).resolve()

    # Create a temporary worktree
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"mcgs-eval-{node_id}-"))

    try:
        # Create worktree from the node's branch
        print(f"Creating worktree for {branch} at {worktree_dir}...")
        _run_git(["worktree", "add", str(worktree_dir), branch], cwd=repo_dir)

        # Check that the script exists
        script_path = worktree_dir / script_name
        if not script_path.exists():
            raise FileNotFoundError(
                f"Script '{script_name}' not found in node {node_id}'s branch. "
                f"Expected at: {script_path}"
            )

        # Run the script
        fid_label = f", fidelity={fidelity}" if fidelity else ""
        print(f"Running {'experiment' if multi_objective else 'objective'} script: {script_name} (timeout={timeout}s{fid_label})...")
        run_env = {**os.environ, **fidelity_env}
        result = subprocess.run(
            [sys.executable, script_name],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )

        # Store stdout/stderr for debugging
        node.stdout = result.stdout[-2000:] if result.stdout else ""  # Keep last 2000 chars
        node.stderr = result.stderr[-2000:] if result.stderr else ""

        if result.returncode != 0:
            print(f"Script failed (exit code {result.returncode})", file=sys.stderr)
            if result.stderr:
                print(f"stderr: {result.stderr[:500]}", file=sys.stderr)
            node.status = "failed"
            save_graph(graph, graph_path)
            return None

        # Parse output from last non-empty stdout line
        stdout_lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not stdout_lines:
            print("Error: Script produced no output", file=sys.stderr)
            node.status = "failed"
            save_graph(graph, graph_path)
            return None

        last_line = stdout_lines[-1]

        # Try JSON first (multi-objective experiment results)
        try:
            experiment_results = json.loads(last_line)
            if isinstance(experiment_results, dict):
                # Store fidelity-specific results if applicable
                if fidelity:
                    node.fidelity_results[fidelity] = experiment_results
                # Always set experiment_results to latest (highest-fidelity) results
                node.experiment_results = experiment_results
                node.status = "evaluated"
                # In multi-objective mode, objective is set later by consensus.py
                # In single-objective fallback, don't set objective here
                save_graph(graph, graph_path)
                print(f"Node {node_id} evaluated: experiment_results = {json.dumps(experiment_results)[:200]}")
                return 0.0  # Placeholder; real score comes from consensus
        except (json.JSONDecodeError, TypeError):
            pass  # Not JSON, try float

        # Fall back to float parsing (single-objective mode)
        try:
            objective_value = float(last_line)
        except ValueError:
            print(
                f"Error: Could not parse output from last line: '{last_line}'",
                file=sys.stderr,
            )
            node.status = "failed"
            save_graph(graph, graph_path)
            return None

        # Success — single-objective mode
        node.objective = objective_value
        node.status = "evaluated"
        save_graph(graph, graph_path)
        print(f"Node {node_id} evaluated: objective = {objective_value:.6f}")
        return objective_value

    except subprocess.TimeoutExpired:
        print(f"Error: Objective script timed out after {timeout}s", file=sys.stderr)
        node.status = "failed"
        node.stderr = f"Timeout after {timeout} seconds"
        save_graph(graph, graph_path)
        return None

    except Exception as e:
        print(f"Error executing node {node_id}: {e}", file=sys.stderr)
        node.status = "failed"
        node.stderr = str(e)
        save_graph(graph, graph_path)
        return None

    finally:
        # Clean up worktree
        try:
            _run_git(["worktree", "remove", str(worktree_dir), "--force"], cwd=repo_dir, check=False)
        except Exception:
            pass
        # Belt-and-suspenders: remove the directory if git didn't
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Execute objective for an MCGS node")
    parser.add_argument("--node-id", type=int, required=True, help="Node ID to evaluate")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--repo-dir", default=".", help="Path to the git repository")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--fidelity", default=None,
                        help="Fidelity tier name (e.g., 'low', 'medium', 'high')")
    args = parser.parse_args()

    result = execute_node(
        graph_path=args.graph,
        node_id=args.node_id,
        repo_dir=args.repo_dir,
        timeout=args.timeout,
        fidelity=args.fidelity,
    )

    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
