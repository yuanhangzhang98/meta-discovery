#!/usr/bin/env python3
"""Multi-fidelity execution engine for MCGS.

Implements the multi-fidelity evaluation strategy from the paper:
- Designs start at low fidelity (fast screening)
- Rule-based promotion: top 50% → medium, top 10% → high
- Fixed evaluation schedule per tier ensures comparable rankings

The experiment script reads MCGS_FIDELITY env var to adjust its budget.

Usage:
    python multi_fidelity.py --graph mcgs_graph.json --node-id 5 --repo-dir .
    python multi_fidelity.py --graph mcgs_graph.json --action promote-sweep --repo-dir .
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, GraphNode, load_graph, save_graph
from execute_node import execute_node


def get_tier_name(level: int, graph: MCGSGraph) -> str:
    """Get tier name for a fidelity level."""
    tiers = graph.config.fidelity_tiers
    if 0 <= level < len(tiers):
        return tiers[level]["name"]
    return "unknown"


def execute_at_fidelity(
    graph_path: str,
    node_id: int,
    repo_dir: str = ".",
) -> Dict[str, Any]:
    """Execute a node at its current fidelity level.

    Returns:
        Summary dict with fidelity, status, and results.
    """
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None:
        return {"error": f"Node {node_id} not found", "status": "failed"}

    tier_name = get_tier_name(node.fidelity_level, graph)
    tiers = graph.config.fidelity_tiers
    timeout = 300
    if node.fidelity_level < len(tiers):
        timeout = tiers[node.fidelity_level].get("timeout", 300)

    result = execute_node(
        graph_path=graph_path,
        node_id=node_id,
        repo_dir=repo_dir,
        timeout=timeout,
        fidelity=tier_name,
    )

    # Reload graph to get updated node
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)

    return {
        "node_id": node_id,
        "fidelity": tier_name,
        "fidelity_level": node.fidelity_level,
        "status": node.status,
        "objective": result,
    }


def check_promotion(
    graph: MCGSGraph,
    node_id: int,
    objectives_dir: str | Path | None = None,
) -> bool:
    """Check if a node qualifies for promotion to the next fidelity tier.

    Uses consensus ranking among nodes at the same fidelity level.
    Promotion thresholds: top 50% → medium, top 10% → high.

    Returns True if the node should be promoted.
    """
    node = graph.get_node(node_id)
    if node is None or node.status != "evaluated":
        return False

    current_level = node.fidelity_level
    thresholds = graph.config.promotion_thresholds
    if current_level >= len(thresholds):
        return False  # Already at max fidelity

    threshold = thresholds[current_level]

    # Get all evaluated nodes at the same fidelity level
    peers = [
        n for n in graph.nodes
        if n.fidelity_level == current_level
        and n.status == "evaluated"
        and n.objective is not None
    ]

    if len(peers) < 2:
        return False  # Not enough peers to rank

    # Rank by objective (lower = better if minimizing)
    if graph.config.minimize:
        peers_sorted = sorted(peers, key=lambda n: n.objective)
    else:
        peers_sorted = sorted(peers, key=lambda n: n.objective, reverse=True)

    # Find node's position
    rank = next((i for i, n in enumerate(peers_sorted) if n.id == node_id), None)
    if rank is None:
        return False

    # Check if in top percentile
    percentile = (rank + 1) / len(peers_sorted)
    return percentile <= threshold


def promote_node(
    graph_path: str,
    node_id: int,
) -> bool:
    """Promote a node to the next fidelity level.

    Returns True if promoted, False if already at max or not found.
    """
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None:
        return False

    max_level = len(graph.config.fidelity_tiers) - 1
    if node.fidelity_level >= max_level:
        return False

    node.fidelity_level += 1
    save_graph(graph, graph_path)
    new_tier = get_tier_name(node.fidelity_level, graph)
    print(f"Node {node_id} promoted to fidelity level {node.fidelity_level} ({new_tier})")
    return True


def promotion_sweep(
    graph_path: str,
    objectives_dir: str | None = None,
    repo_dir: str = ".",
    execute_promoted: bool = True,
) -> Dict[str, Any]:
    """Check all evaluated nodes for promotion and optionally re-execute promoted ones.

    Returns:
        Summary with promoted node IDs and re-execution results.
    """
    graph = load_graph(graph_path)
    if not graph.config.multi_fidelity:
        return {"promoted": [], "message": "Multi-fidelity not enabled"}

    obj_dir = objectives_dir or graph.config.objectives_dir
    promoted = []

    for node in graph.nodes:
        if node.status != "evaluated" or node.objective is None:
            continue
        max_level = len(graph.config.fidelity_tiers) - 1
        if node.fidelity_level >= max_level:
            continue

        if check_promotion(graph, node.id, obj_dir):
            if promote_node(graph_path, node.id):
                promoted.append(node.id)
                # Reload graph after promotion
                graph = load_graph(graph_path)

    # Re-execute promoted nodes at new fidelity
    execution_results = []
    if execute_promoted:
        for nid in promoted:
            result = execute_at_fidelity(graph_path, nid, repo_dir)
            execution_results.append(result)

    return {
        "promoted": promoted,
        "execution_results": execution_results,
        "total_checked": len([n for n in graph.nodes if n.status == "evaluated"]),
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-fidelity execution for MCGS")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--repo-dir", default=".", help="Path to the git repository")
    parser.add_argument("--objectives-dir", default=None, help="Path to objectives directory")

    sub = parser.add_subparsers(dest="action", required=True)

    # Execute a single node at its current fidelity
    p_exec = sub.add_parser("execute", help="Execute a node at its current fidelity level")
    p_exec.add_argument("--node-id", type=int, required=True)

    # Check and promote nodes
    p_sweep = sub.add_parser("promote-sweep", help="Check all nodes for promotion")
    p_sweep.add_argument("--no-execute", action="store_true",
                         help="Don't re-execute promoted nodes")

    # Check promotion for a single node
    p_check = sub.add_parser("check", help="Check if a node qualifies for promotion")
    p_check.add_argument("--node-id", type=int, required=True)

    args = parser.parse_args()

    if args.action == "execute":
        result = execute_at_fidelity(args.graph, args.node_id, args.repo_dir)
        print(json.dumps(result, indent=2))

    elif args.action == "promote-sweep":
        result = promotion_sweep(
            args.graph,
            objectives_dir=args.objectives_dir,
            repo_dir=args.repo_dir,
            execute_promoted=not args.no_execute,
        )
        print(json.dumps(result, indent=2))

    elif args.action == "check":
        graph = load_graph(args.graph)
        qualifies = check_promotion(graph, args.node_id, args.objectives_dir)
        print(json.dumps({"node_id": args.node_id, "qualifies_for_promotion": qualifies}))


if __name__ == "__main__":
    main()
