#!/usr/bin/env python3
"""Initialize MCGS in a repository.

Sets up the git structure and creates the baseline node (node-0).
Supports both single-objective mode (default) and multi-objective mode.

Usage:
    # Single-objective mode (backward compatible):
    python init_mcgs.py --repo-dir . --objective-script evaluate.py --research-goal "..."

    # Multi-objective mode:
    python init_mcgs.py --repo-dir . --experiment-script run_experiment.py --research-goal "..."

What this does:
    1. Initialize git if not already a repo
    2. Create the baseline branch mcgs/node-0 from current HEAD
    3. Create mcgs_graph.json with config and node-0
    4. (Multi-objective) Create objectives directory
    5. Commit the graph metadata
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import (
    MCGSGraph,
    GraphConfig,
    GraphNode,
    ObjectiveMeta,
    save_graph,
    load_graph,
    git_init,
    git_branch_exists,
    git_create_branch,
    git_checkout,
    git_commit_all,
    git_current_branch,
    _run_git,
)


def init_mcgs(
    repo_dir: str = ".",
    objective_script: str = "evaluate.py",
    research_goal: str = "",
    minimize: bool = True,
    c_puct: float = 0.1,
    decay_factor: float = 0.9,
    experiment_script: str = "",
    objectives_dir: str = "mcgs_objectives",
    objective_interval: int = 5,
    meta_interval: int = 10,
    age_decay: float = 0.9,
    initial_objective_code: str = "",
    initial_objective_description: str = "",
) -> MCGSGraph:
    """Initialize MCGS in the given repository.

    Args:
        repo_dir: Path to the repository
        objective_script: Name of the objective evaluation script (single-objective mode)
        research_goal: High-level description of the research goal
        minimize: Whether to minimize (True) or maximize (False) the objective
        c_puct: UCB exploration constant
        decay_factor: Visit count propagation decay
        experiment_script: Name of experiment script outputting JSON (multi-objective mode)
        objectives_dir: Directory to store objective .py files
        objective_interval: Generate new objective every N iterations
        meta_interval: Run meta-agent analysis every N iterations
        age_decay: Lambda for objective age decay in consensus
        initial_objective_code: Python code for the initial objective function
        initial_objective_description: Description of the initial objective

    Returns:
        The initialized MCGSGraph
    """
    repo_dir = Path(repo_dir).resolve()
    graph_path = repo_dir / "mcgs_graph.json"

    # Check for existing MCGS setup
    if graph_path.exists():
        existing = load_graph(graph_path)
        if existing.nodes:
            print(f"MCGS already initialized with {len(existing.nodes)} nodes.")
            print("To reset, delete mcgs_graph.json and mcgs/* branches first.")
            return existing

    # Step 1: Ensure git is initialized with at least one commit
    print("Ensuring git repository is initialized...")
    git_init(repo_dir)

    # Save the original branch to return to later
    original_branch = git_current_branch(repo_dir)

    # Step 2: Create the baseline branch mcgs/node-0
    node_0_branch = "mcgs/node-0"
    if not git_branch_exists(node_0_branch, repo_dir):
        print(f"Creating baseline branch: {node_0_branch}")
        git_create_branch(node_0_branch, "HEAD", repo_dir)
    else:
        print(f"Baseline branch {node_0_branch} already exists.")

    # Step 3: Create the graph metadata
    multi_objective = bool(experiment_script)
    config = GraphConfig(
        c_puct=c_puct,
        decay_factor=decay_factor,
        objective_script=objective_script,
        research_goal=research_goal,
        minimize=minimize,
        experiment_script=experiment_script,
        objectives_dir=objectives_dir,
        objective_interval=objective_interval,
        meta_interval=meta_interval,
        age_decay=age_decay,
    )

    node_0 = GraphNode(
        id=0,
        branch=node_0_branch,
        short_name="baseline",
        parent_edges=[],
        timestamp=datetime.now(timezone.utc).isoformat(),
        description="Initial baseline implementation",
        status="pending",
    )

    graph = MCGSGraph(
        config=config,
        nodes=[node_0],
        next_id=1,
        total_iterations=0,
    )

    # Step 4: Multi-objective setup — create objectives directory and initial objective
    if multi_objective:
        obj_dir = repo_dir / objectives_dir
        obj_dir.mkdir(parents=True, exist_ok=True)

        if initial_objective_code:
            obj_filename = "objective_0.py"
            obj_path = obj_dir / obj_filename
            obj_path.write_text(initial_objective_code, encoding="utf-8")

            obj_meta = ObjectiveMeta(
                id=0,
                name=initial_objective_description.split()[0].lower() if initial_objective_description else "baseline_objective",
                filename=obj_filename,
                description=initial_objective_description or "Initial baseline objective function",
                created_iteration=0,
            )
            graph.objectives.append(obj_meta)
            print(f"  Created initial objective: {obj_path}")

    # Step 5: Save graph.json and commit
    save_graph(graph, graph_path)

    # Commit the graph file on the current branch
    git_commit_all("Initialize MCGS graph with baseline node-0", repo_dir)

    # Also commit graph.json on the node-0 branch
    git_checkout(node_0_branch, repo_dir)
    save_graph(graph, graph_path)
    git_commit_all("Initialize MCGS graph with baseline node-0", repo_dir)

    # Return to original branch
    git_checkout(original_branch, repo_dir)

    print(f"\nMCGS initialized successfully!")
    print(f"  Repository: {repo_dir}")
    print(f"  Baseline branch: {node_0_branch}")
    if multi_objective:
        print(f"  Mode: multi-objective")
        print(f"  Experiment script: {experiment_script}")
        print(f"  Objectives dir: {objectives_dir}")
        print(f"  Objective interval: every {objective_interval} iterations")
        print(f"  Meta-agent interval: every {meta_interval} iterations")
    else:
        print(f"  Mode: single-objective")
        print(f"  Objective script: {objective_script}")
    print(f"  Research goal: {research_goal[:80]}...")
    print(f"  Direction: {'minimize' if minimize else 'maximize'}")
    print(f"  Graph file: {graph_path}")
    print(f"\nNext step: evaluate the baseline by running:")
    print(f"  python scripts/execute_node.py --node-id 0 --graph mcgs_graph.json --repo-dir {repo_dir}")

    return graph


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Initialize MCGS in a repository")
    parser.add_argument("--repo-dir", default=".", help="Path to the repository")
    parser.add_argument("--objective-script", default="evaluate.py",
                        help="Name of the objective evaluation script (single-objective mode)")
    parser.add_argument("--research-goal", default="", help="High-level research goal")
    parser.add_argument("--minimize", action="store_true", default=True,
                        help="Minimize the objective (default)")
    parser.add_argument("--maximize", action="store_true",
                        help="Maximize the objective instead of minimizing")
    parser.add_argument("--c-puct", type=float, default=0.1,
                        help="UCB exploration constant")
    parser.add_argument("--decay", type=float, default=0.9,
                        help="Visit count propagation decay factor")
    # Multi-objective options
    parser.add_argument("--experiment-script", default="",
                        help="Experiment script outputting JSON (enables multi-objective mode)")
    parser.add_argument("--objectives-dir", default="mcgs_objectives",
                        help="Directory for objective function files")
    parser.add_argument("--objective-interval", type=int, default=5,
                        help="Generate new objective every N iterations")
    parser.add_argument("--meta-interval", type=int, default=10,
                        help="Run meta-agent analysis every N iterations")
    parser.add_argument("--age-decay", type=float, default=0.9,
                        help="Age decay factor for objective weighting")
    args = parser.parse_args()

    minimize = not args.maximize

    init_mcgs(
        repo_dir=args.repo_dir,
        objective_script=args.objective_script,
        research_goal=args.research_goal,
        minimize=minimize,
        c_puct=args.c_puct,
        decay_factor=args.decay,
        experiment_script=args.experiment_script,
        objectives_dir=args.objectives_dir,
        objective_interval=args.objective_interval,
        meta_interval=args.meta_interval,
        age_decay=args.age_decay,
    )


if __name__ == "__main__":
    main()
