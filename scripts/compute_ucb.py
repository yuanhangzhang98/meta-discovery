#!/usr/bin/env python3
"""Compute UCB scores for all nodes in the MCGS graph.

Implements the full MCGS algorithm from the paper:
  1. Rebuild visit counts from scratch via weighted BFS propagation
  2. Compute rank scores from objective values
  3. Compute UCB = rank_score + c * sqrt(N_total) / (1 + n_j)

Usage:
    python compute_ucb.py --graph mcgs_graph.json [--c-puct 0.1] [--decay 0.9]
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import deque
from pathlib import Path
from typing import Dict, List, Tuple

# Allow importing sibling modules when run as script
sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, GraphNode, load_graph, save_graph


def propagate_visit_counts(graph: MCGSGraph) -> None:
    """Rebuild all visit counts from scratch using weighted BFS propagation.

    For each node that has parent edges, propagate credit upward through the
    ancestry graph. Each hop is attenuated by:
        ancestor_edge_weight * decay_factor^(depth+1)

    This concentrates credit near immediate parents while still giving some
    credit to more distant ancestors.
    """
    decay = graph.config.decay_factor
    min_contrib = graph.config.min_contribution

    # Reset all visit counts to baseline (1.0)
    for node in graph.nodes:
        node.visit_count = 1.0

    # Build a lookup for fast access
    node_map: Dict[int, GraphNode] = {n.id: n for n in graph.nodes}

    # For each node that has parents, propagate credit upward
    for node in graph.nodes:
        if not node.parent_edges:
            continue

        # BFS upward from this node's parents
        # Queue items: (ancestor_id, credit, depth)
        visited: Dict[int, float] = {}
        queue: deque[Tuple[int, float, int]] = deque()

        for edge in node.parent_edges:
            if edge.weight > 0 and edge.node_id in node_map:
                queue.append((edge.node_id, edge.weight, 0))

        while queue:
            ancestor_id, credit, depth = queue.popleft()
            if credit <= 0:
                continue

            # Accumulate credit for this ancestor
            visited[ancestor_id] = visited.get(ancestor_id, 0.0) + credit

            # Propagate further upward
            ancestor = node_map.get(ancestor_id)
            if ancestor is None:
                continue

            next_depth = depth + 1
            for edge in ancestor.parent_edges:
                grandparent_id = edge.node_id
                if grandparent_id not in node_map:
                    continue
                combined = credit * edge.weight * (decay ** next_depth)
                if combined < min_contrib:
                    continue
                queue.append((grandparent_id, combined, next_depth))

        # Apply accumulated credit
        for ancestor_id, contribution in visited.items():
            ancestor_node = node_map.get(ancestor_id)
            if ancestor_node is not None:
                ancestor_node.visit_count += contribution


def compute_rank_scores(graph: MCGSGraph) -> None:
    """Compute normalized rank scores from objective values.

    Rank scores are in [0, 1] where 1.0 = best objective.
    For minimization: lower objective -> higher rank score.
    For maximization: higher objective -> higher rank score.
    Nodes without objectives get rank_score = 0.0.
    """
    # Collect evaluated nodes
    evaluated = [(n.objective, n.id) for n in graph.nodes if n.objective is not None]

    if len(evaluated) <= 1:
        # All get same rank if 0 or 1 evaluated
        for node in graph.nodes:
            node.rank_score = 1.0 if node.objective is not None else 0.0
        return

    # Sort: for minimization, reverse=True so worst (highest) objective comes first
    # Then best (lowest) gets highest rank index -> highest rank score
    if graph.config.minimize:
        sorted_evals = sorted(evaluated, key=lambda x: x[0], reverse=True)
    else:
        sorted_evals = sorted(evaluated, key=lambda x: x[0], reverse=False)

    total = len(sorted_evals)
    rank_map: Dict[int, float] = {}
    for idx, (_, node_id) in enumerate(sorted_evals):
        rank_map[node_id] = idx / (total - 1)  # 0.0 = worst, 1.0 = best

    # Assign scores
    for node in graph.nodes:
        node.rank_score = rank_map.get(node.id, 0.0)


def compute_ucb_scores(graph: MCGSGraph) -> None:
    """Compute UCB scores: UCB_j = r_j + c * sqrt(N_total) / (1 + n_j).

    Higher UCB = more interesting to explore (balances exploitation and exploration).
    """
    c = graph.config.c_puct
    total_visits = sum(n.visit_count for n in graph.nodes)
    if total_visits <= 0:
        total_visits = 1.0

    sqrt_total = math.sqrt(total_visits)

    for node in graph.nodes:
        exploration = c * sqrt_total / (1.0 + node.visit_count)
        node.ucb_score = node.rank_score + exploration


def update_all_scores(graph: MCGSGraph) -> None:
    """Run the full UCB computation pipeline: visit counts -> ranks -> UCB."""
    propagate_visit_counts(graph)
    compute_rank_scores(graph)
    compute_ucb_scores(graph)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compute UCB scores for MCGS graph")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--c-puct", type=float, default=None, help="Override exploration constant")
    parser.add_argument("--decay", type=float, default=None, help="Override decay factor")
    args = parser.parse_args()

    graph = load_graph(args.graph)

    # Apply overrides if provided
    if args.c_puct is not None:
        graph.config.c_puct = args.c_puct
    if args.decay is not None:
        graph.config.decay_factor = args.decay

    # Compute scores
    update_all_scores(graph)

    # Save
    save_graph(graph, args.graph)

    # Print summary
    nodes_by_ucb = sorted(graph.nodes, key=lambda n: n.ucb_score, reverse=True)
    print(f"Updated {len(graph.nodes)} nodes. Top 10 by UCB:")
    print(f"{'ID':>4} {'Name':<30} {'Obj':>10} {'Visits':>8} {'Rank':>6} {'UCB':>8}")
    print("-" * 72)
    for n in nodes_by_ucb[:10]:
        obj_str = f"{n.objective:.4f}" if n.objective is not None else "N/A"
        print(f"{n.id:>4} {n.short_name:<30} {obj_str:>10} {n.visit_count:>8.2f} {n.rank_score:>6.3f} {n.ucb_score:>8.4f}")


if __name__ == "__main__":
    main()
