#!/usr/bin/env python3
"""Evaluate all active objectives on a single node's experiment results.

This is a lightweight script that updates a node's objective_scores without
recomputing the full consensus. Use it after execute_node.py to populate
per-objective scores for a newly evaluated node.

Usage:
    python run_objectives.py --node-id 5 --graph mcgs_graph.json [--objectives-dir mcgs_objectives/]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import load_graph, save_graph
from consensus import load_objective_function


def run_objectives_on_node(
    graph_path: str,
    node_id: int,
    objectives_dir: str | None = None,
) -> dict | None:
    """Evaluate all active objectives on a single node.

    Args:
        graph_path: Path to mcgs_graph.json
        node_id: ID of the node to score
        objectives_dir: Path to objectives directory (default: from graph config)

    Returns:
        Dict of obj_name -> score if successful, None if failed.
    """
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)

    if node is None:
        print(f"Error: Node {node_id} not found", file=sys.stderr)
        return None

    if node.experiment_results is None:
        print(f"Error: Node {node_id} has no experiment_results", file=sys.stderr)
        return None

    obj_dir = Path(objectives_dir) if objectives_dir else Path(graph.config.objectives_dir)
    active_objectives = graph.get_active_objectives()

    if not active_objectives:
        print("No active objectives to evaluate")
        return {}

    scores = {}
    for obj_meta in active_objectives:
        obj_path = obj_dir / obj_meta.filename
        try:
            obj_fn = load_objective_function(obj_path)
            score = obj_fn(node.experiment_results)
            if score is None or score != score:  # None or NaN
                score = float("inf")
            scores[obj_meta.name] = float(score)
        except Exception as e:
            print(f"Warning: Objective {obj_meta.name} failed on node {node_id}: {e}",
                  file=sys.stderr)
            scores[obj_meta.name] = float("inf")

    node.objective_scores = scores
    save_graph(graph, graph_path)

    print(f"Node {node_id} scored by {len(scores)} objectives:")
    for name, score in sorted(scores.items()):
        score_str = f"{score:.6f}" if score != float("inf") else "inf (failed)"
        print(f"  {name}: {score_str}")

    return scores


def main():
    parser = argparse.ArgumentParser(description="Evaluate objectives on a single MCGS node")
    parser.add_argument("--node-id", type=int, required=True, help="Node ID to score")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--objectives-dir", default=None,
                        help="Path to objectives directory (default: from graph config)")
    args = parser.parse_args()

    result = run_objectives_on_node(
        graph_path=args.graph,
        node_id=args.node_id,
        objectives_dir=args.objectives_dir,
    )

    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
