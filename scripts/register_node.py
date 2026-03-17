#!/usr/bin/env python3
"""Register a new node in the MCGS graph.

Handles node creation, parent edge validation, and graph persistence.
Eliminates the manual boilerplate the orchestrator previously had to write
for every iteration.

Usage:
    python register_node.py \
        --graph mcgs_graph.json \
        --short-name "sigmoid_gate" \
        --description "Added sigmoid gating to loss" \
        --branch mcgs/node-5 \
        --parent-edges '[{"node_id": 3, "weight": 0.7}, {"node_id": 7, "weight": 0.3}]'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import ParentEdge, load_graph, save_graph


def register_node(
    graph_path: str,
    short_name: str,
    branch: str,
    parent_edges_json: str,
    description: str = "",
    increment_iteration: bool = True,
    is_hpo_tuned: bool = False,
) -> int:
    """Register a new node in the MCGS graph.

    Args:
        graph_path: Path to mcgs_graph.json
        short_name: Short descriptive name (≤40 chars)
        branch: Git branch name (e.g., mcgs/node-5)
        parent_edges_json: JSON string of parent edges
        description: Description of the modification
        increment_iteration: Whether to increment total_iterations
        is_hpo_tuned: Whether this node was created by HPO

    Returns:
        The new node's ID.
    """
    graph = load_graph(graph_path)

    # Parse and validate parent edges
    raw_edges = json.loads(parent_edges_json)
    if not isinstance(raw_edges, list):
        print("Error: parent-edges must be a JSON array", file=sys.stderr)
        sys.exit(1)

    edges = []
    total_weight = 0.0
    for entry in raw_edges:
        nid = entry.get("node_id")
        w = entry.get("weight")
        if nid is None or w is None:
            print(f"Error: each parent edge must have 'node_id' and 'weight': {entry}",
                  file=sys.stderr)
            sys.exit(1)
        edges.append(ParentEdge(node_id=int(nid), weight=float(w)))
        total_weight += float(w)

    # Warn if weights don't sum to 1.0
    if edges and abs(total_weight - 1.0) > 0.01:
        print(f"Warning: parent edge weights sum to {total_weight:.3f}, expected 1.0",
              file=sys.stderr)

    # Create the node
    node = graph.add_node(
        short_name=short_name,
        branch=branch,
        parent_edges=edges,
        description=description,
    )
    node.is_hpo_tuned = is_hpo_tuned

    if increment_iteration:
        graph.total_iterations += 1

    save_graph(graph, graph_path)

    print(json.dumps({
        "node_id": node.id,
        "branch": node.branch,
        "short_name": node.short_name,
        "total_iterations": graph.total_iterations,
    }))
    return node.id


def main():
    parser = argparse.ArgumentParser(description="Register a new MCGS node")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--short-name", required=True, help="Short descriptive name (≤40 chars)")
    parser.add_argument("--branch", required=True, help="Git branch name")
    parser.add_argument("--parent-edges", required=True,
                        help='JSON array of parent edges: [{"node_id": 3, "weight": 0.7}, ...]')
    parser.add_argument("--description", default="", help="Description of the modification")
    parser.add_argument("--no-increment", action="store_true",
                        help="Don't increment total_iterations")
    parser.add_argument("--hpo-tuned", action="store_true",
                        help="Mark node as HPO-tuned")
    args = parser.parse_args()

    register_node(
        graph_path=args.graph,
        short_name=args.short_name,
        branch=args.branch,
        parent_edges_json=args.parent_edges,
        description=args.description,
        increment_iteration=not args.no_increment,
        is_hpo_tuned=args.hpo_tuned,
    )


if __name__ == "__main__":
    main()
