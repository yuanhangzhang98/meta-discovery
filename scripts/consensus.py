#!/usr/bin/env python3
"""Consensus objective aggregation for multi-objective MCGS.

Combines multiple objective functions into a single consensus ranking using
Kendall tau correlation-weighted voting (Borda count). This implements the
consensus objective algorithm from the reference paper:

  1. Score matrix: evaluate all objectives on all designs
  2. Rank conversion: lower score = better rank (0-indexed)
  3. Kendall tau matrix: pairwise rank correlations between objectives
  4. Weights: agreement (median tau, clamped >= 0) × age decay × meta-weight
  5. Consensus score: weighted average of normalized ranks (lower = better)

Usage:
    python consensus.py --graph mcgs_graph.json --objectives-dir mcgs_objectives/ [--verbose]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, ObjectiveMeta, load_graph, save_graph


# ──────────────────────────────────────────────────────────────────────────────
# Objective loading
# ──────────────────────────────────────────────────────────────────────────────

def load_objective_function(filepath: Path) -> Callable[[Dict[str, Any]], float]:
    """Dynamically import an objective .py file and return its `objective` function.

    The file must define: def objective(experiment_results: dict) -> float
    """
    spec = importlib.util.spec_from_file_location(f"mcgs_obj_{filepath.stem}", filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load objective from {filepath}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "objective"):
        raise AttributeError(f"Objective file {filepath} must define a function named 'objective'")
    return module.objective


# ──────────────────────────────────────────────────────────────────────────────
# Step 1: Score matrix
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_all_objectives(
    graph: MCGSGraph,
    objectives_dir: Path,
) -> Tuple[Dict[str, Dict[int, float]], List[str], List[int]]:
    """Evaluate all active objectives on all nodes with experiment_results.

    Returns:
        score_matrix: obj_name -> node_id -> score
        objective_names: list of objective names evaluated
        node_ids: list of node IDs with experiment_results
    """
    active_objectives = graph.get_active_objectives()
    if not active_objectives:
        return {}, [], []

    # Collect nodes that have experiment results
    evaluated_nodes = [n for n in graph.nodes if n.experiment_results is not None]
    if not evaluated_nodes:
        return {}, [o.name for o in active_objectives], []

    node_ids = [n.id for n in evaluated_nodes]
    score_matrix: Dict[str, Dict[int, float]] = {}
    objective_names: List[str] = []

    for obj_meta in active_objectives:
        obj_path = objectives_dir / obj_meta.filename
        try:
            obj_fn = load_objective_function(obj_path)
        except Exception as e:
            print(f"Warning: Failed to load objective {obj_meta.name}: {e}", file=sys.stderr)
            continue

        objective_names.append(obj_meta.name)
        score_matrix[obj_meta.name] = {}

        for node in evaluated_nodes:
            try:
                score = obj_fn(node.experiment_results)
                if score is None or score != score:  # None or NaN
                    score = float("inf")
                elif not isinstance(score, (int, float)):
                    score = float("inf")
            except Exception as e:
                print(f"Warning: Objective {obj_meta.name} failed on node {node.id}: {e}",
                      file=sys.stderr)
                score = float("inf")

            score_matrix[obj_meta.name][node.id] = float(score)

    return score_matrix, objective_names, node_ids


# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Rank conversion
# ──────────────────────────────────────────────────────────────────────────────

def build_ranking_matrix(
    score_matrix: Dict[str, Dict[int, float]],
) -> Dict[str, Dict[int, int]]:
    """Convert score matrix to ranking matrix (lower score = rank 0 = best).

    Ties broken by node_id for stability.
    """
    ranking_matrix: Dict[str, Dict[int, int]] = {}

    for obj_name, scores in score_matrix.items():
        sorted_nodes = sorted(scores.items(), key=lambda x: (x[1], x[0]))
        ranking_matrix[obj_name] = {
            node_id: rank for rank, (node_id, _) in enumerate(sorted_nodes)
        }

    return ranking_matrix


# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Kendall tau matrix
# ──────────────────────────────────────────────────────────────────────────────

def _kendall_tau_pure(ranks_a: List[int], ranks_b: List[int]) -> float:
    """Compute Kendall tau-b correlation between two rank lists (pure Python fallback).

    Returns value in [-1, 1]. Returns 0.0 for degenerate cases.
    """
    n = len(ranks_a)
    if n < 2:
        return 0.0

    concordant = 0
    discordant = 0
    ties_a = 0
    ties_b = 0

    for i in range(n):
        for j in range(i + 1, n):
            diff_a = ranks_a[i] - ranks_a[j]
            diff_b = ranks_b[i] - ranks_b[j]

            if diff_a == 0:
                ties_a += 1
                if diff_b == 0:
                    ties_b += 1
                continue
            if diff_b == 0:
                ties_b += 1
                continue

            if (diff_a > 0 and diff_b > 0) or (diff_a < 0 and diff_b < 0):
                concordant += 1
            else:
                discordant += 1

    n_pairs = n * (n - 1) // 2
    denom_a = n_pairs - ties_a
    denom_b = n_pairs - ties_b

    if denom_a == 0 or denom_b == 0:
        return 0.0

    return (concordant - discordant) / (denom_a * denom_b) ** 0.5


def _kendall_tau(ranks_a: List[int], ranks_b: List[int]) -> float:
    """Compute Kendall tau using scipy if available, else pure Python."""
    try:
        from scipy.stats import kendalltau
        tau, _ = kendalltau(ranks_a, ranks_b)
        if tau != tau:  # NaN
            return 0.0
        return float(tau)
    except ImportError:
        return _kendall_tau_pure(ranks_a, ranks_b)


def compute_kendall_tau_matrix(
    ranking_matrix: Dict[str, Dict[int, int]],
    objective_names: List[str],
) -> Dict[Tuple[str, str], float]:
    """Compute pairwise Kendall tau correlations between all objectives.

    Returns:
        tau_matrix: (obj_i, obj_j) -> tau value in [-1, 1]
    """
    tau_matrix: Dict[Tuple[str, str], float] = {}

    if len(objective_names) < 2:
        return tau_matrix

    # Get common node IDs across all objectives
    common_ids = set.intersection(
        *[set(ranking_matrix[name].keys()) for name in objective_names]
    )
    node_id_list = sorted(common_ids)

    if len(node_id_list) < 2:
        return tau_matrix

    for i, name_i in enumerate(objective_names):
        for name_j in objective_names[i + 1:]:
            ranks_i = [ranking_matrix[name_i][nid] for nid in node_id_list]
            ranks_j = [ranking_matrix[name_j][nid] for nid in node_id_list]

            tau = _kendall_tau(ranks_i, ranks_j)
            tau_matrix[(name_i, name_j)] = tau
            tau_matrix[(name_j, name_i)] = tau

    return tau_matrix


# ──────────────────────────────────────────────────────────────────────────────
# Step 4: Objective weights
# ──────────────────────────────────────────────────────────────────────────────

def compute_objective_weights(
    tau_matrix: Dict[Tuple[str, str], float],
    objective_names: List[str],
    objectives: List[ObjectiveMeta],
    current_iteration: int,
    age_decay: float = 0.9,
) -> Dict[str, float]:
    """Compute weights for each objective: agreement × age_decay × meta_weight.

    Agreement = max(median tau with others, 0) — suppresses outlier objectives.
    Age decay = age_decay^(current_iter - created_iter) — phases out old objectives.
    Meta weight = objective.weight — set by meta-agent analysis.
    """
    if not objective_names:
        return {}

    # Build name -> ObjectiveMeta lookup
    obj_by_name = {o.name: o for o in objectives}

    weights: Dict[str, float] = {}

    for name in objective_names:
        obj_meta = obj_by_name.get(name)
        if obj_meta is None:
            continue

        # Agreement: median Kendall tau with all other objectives
        if len(objective_names) == 1:
            agreement = 1.0
        else:
            tau_values = [
                tau_matrix.get((name, other), 0.0)
                for other in objective_names
                if other != name
            ]
            if tau_values:
                sorted_taus = sorted(tau_values)
                mid = len(sorted_taus) // 2
                if len(sorted_taus) % 2 == 0:
                    median_tau = (sorted_taus[mid - 1] + sorted_taus[mid]) / 2.0
                else:
                    median_tau = sorted_taus[mid]
            else:
                median_tau = 0.0
            agreement = max(median_tau, 0.0)

        # Age decay
        age = max(current_iteration - obj_meta.created_iteration, 0)
        decay = age_decay ** age

        # Meta-agent weight multiplier
        meta_weight = obj_meta.weight

        weights[name] = agreement * decay * meta_weight

    # Normalize to sum to 1
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    else:
        # Fallback: equal weights
        n = len(objective_names)
        weights = {name: 1.0 / n for name in objective_names}

    return weights


# ──────────────────────────────────────────────────────────────────────────────
# Step 5: Consensus score (weighted Borda count)
# ──────────────────────────────────────────────────────────────────────────────

def build_consensus_scores(
    ranking_matrix: Dict[str, Dict[int, int]],
    objective_weights: Dict[str, float],
    node_ids: List[int],
) -> Dict[int, float]:
    """Compute consensus score for each node via weighted Borda count.

    Returns:
        node_id -> consensus score in [0, 1] (lower = better)
    """
    if not node_ids or not ranking_matrix:
        return {}

    num_nodes = len(node_ids)
    consensus: Dict[int, float] = {}

    for node_id in node_ids:
        weighted_rank = 0.0
        weight_sum = 0.0

        for obj_name, weight in objective_weights.items():
            if obj_name not in ranking_matrix:
                continue
            if node_id not in ranking_matrix[obj_name]:
                continue

            rank = ranking_matrix[obj_name][node_id]
            # Normalize rank to [0, 1]
            normalized = rank / (num_nodes - 1) if num_nodes > 1 else 0.0

            weighted_rank += weight * normalized
            weight_sum += weight

        if weight_sum > 0:
            consensus[node_id] = weighted_rank / weight_sum
        else:
            consensus[node_id] = 1.0  # Worst possible

    return consensus


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end pipeline
# ──────────────────────────────────────────────────────────────────────────────

def compute_consensus(
    graph: MCGSGraph,
    objectives_dir: Path,
) -> Dict[str, Any]:
    """Run the full consensus pipeline and return stats.

    Does NOT modify the graph — call update_graph_with_consensus() for that.

    Returns:
        stats dict with keys: weights, tau_matrix, num_objectives, num_designs,
        consensus_scores, objective_names
    """
    active = graph.get_active_objectives()
    if not active:
        return {
            "weights": {},
            "tau_matrix": {},
            "num_objectives": 0,
            "num_designs": 0,
            "consensus_scores": {},
            "objective_names": [],
            "meta_weights_applied": False,
        }

    # Step 1: Score matrix
    score_matrix, objective_names, node_ids = evaluate_all_objectives(graph, objectives_dir)

    if len(objective_names) == 0 or len(node_ids) == 0:
        return {
            "weights": {},
            "tau_matrix": {},
            "num_objectives": len(objective_names),
            "num_designs": len(node_ids),
            "consensus_scores": {},
            "objective_names": objective_names,
            "meta_weights_applied": False,
        }

    # Step 2: Ranking matrix
    ranking_matrix = build_ranking_matrix(score_matrix)

    # Step 3: Kendall tau matrix
    tau_matrix = compute_kendall_tau_matrix(ranking_matrix, objective_names)

    # Step 4: Objective weights
    weights = compute_objective_weights(
        tau_matrix,
        objective_names,
        active,
        current_iteration=graph.total_iterations,
        age_decay=graph.config.age_decay,
    )

    # Step 5: Consensus scores
    consensus_scores = build_consensus_scores(ranking_matrix, weights, node_ids)

    # Check if meta-weights are non-default
    meta_applied = any(o.weight != 1.0 for o in active)

    # Format tau matrix for JSON serialization
    tau_dict = {f"{k[0]}|{k[1]}": v for k, v in tau_matrix.items()}

    return {
        "weights": weights,
        "tau_matrix": tau_dict,
        "num_objectives": len(objective_names),
        "num_designs": len(node_ids),
        "consensus_scores": consensus_scores,
        "objective_names": objective_names,
        "score_matrix": {name: {str(nid): s for nid, s in scores.items()}
                         for name, scores in score_matrix.items()},
        "meta_weights_applied": meta_applied,
    }


def update_graph_with_consensus(
    graph: MCGSGraph,
    objectives_dir: Path,
) -> Dict[str, Any]:
    """Run consensus pipeline and update graph nodes.

    Sets node.consensus_score and node.objective (to consensus score) for all
    nodes with experiment_results. Also updates node.objective_scores with
    per-objective values.

    Returns stats dict from compute_consensus().
    """
    stats = compute_consensus(graph, objectives_dir)
    consensus_scores = stats.get("consensus_scores", {})
    score_matrix = stats.get("score_matrix", {})

    for node in graph.nodes:
        if node.id in consensus_scores:
            node.consensus_score = consensus_scores[node.id]
            # Set objective to consensus score so compute_ucb.py works unchanged
            node.objective = consensus_scores[node.id]

        # Update per-objective scores
        if node.experiment_results is not None:
            obj_scores = {}
            for obj_name, scores in score_matrix.items():
                node_key = str(node.id)
                if node_key in scores:
                    obj_scores[obj_name] = scores[node_key]
            if obj_scores:
                node.objective_scores = obj_scores

    return stats


def format_tau_matrix(
    tau_matrix: Dict[str, float],
    objective_names: List[str],
) -> str:
    """Format the Kendall tau correlation matrix as a readable markdown table."""
    if len(objective_names) < 2:
        return "Not enough objectives for correlation analysis."

    # Header
    lines = ["| Objective |"]
    header_sep = "|---|"
    for name in objective_names:
        short = name[:12]
        lines[0] += f" {short} |"
        header_sep += "---|"
    lines.append(header_sep)

    # Rows
    for name_i in objective_names:
        row = f"| {name_i[:12]} |"
        for name_j in objective_names:
            if name_i == name_j:
                row += " 1.00 |"
            else:
                key = f"{name_i}|{name_j}"
                tau = tau_matrix.get(key, 0.0)
                row += f" {tau:.2f} |"
        lines.append(row)

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compute consensus objective for MCGS")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--objectives-dir", default=None,
                        help="Path to objectives directory (default: from graph config)")
    parser.add_argument("--verbose", action="store_true", help="Print detailed output")
    args = parser.parse_args()

    graph = load_graph(args.graph)

    # Resolve objectives directory
    if args.objectives_dir:
        objectives_dir = Path(args.objectives_dir)
    else:
        objectives_dir = Path(graph.config.objectives_dir)

    if not objectives_dir.exists():
        print(f"Objectives directory not found: {objectives_dir}", file=sys.stderr)
        sys.exit(1)

    # Run consensus and update graph
    stats = update_graph_with_consensus(graph, objectives_dir)
    save_graph(graph, args.graph)

    # Print summary
    from graph_utils import format_consensus_summary
    print(format_consensus_summary(stats))

    if args.verbose:
        print()
        objective_names = stats.get("objective_names", [])
        tau_matrix = stats.get("tau_matrix", {})
        print(format_tau_matrix(tau_matrix, objective_names))

        print("\nConsensus scores:")
        for node_id, score in sorted(stats.get("consensus_scores", {}).items(),
                                      key=lambda x: x[1]):
            node = graph.get_node(node_id)
            name = node.short_name if node else "?"
            print(f"  Node {node_id} ({name}): {score:.4f}")


if __name__ == "__main__":
    main()
