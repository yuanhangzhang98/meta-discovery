#!/usr/bin/env python3
"""Combined objective analysis visualization for multi-objective MCGS.

Generates a single multi-panel figure with:
  (a) Kendall tau pairwise heatmap between objectives
  (b) Objective weights bar chart
  (c) PCA embedding of objectives (if >=3 objectives and sklearn available)

Skipped entirely in single-objective mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.cm import ScalarMappable
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, load_graph
from consensus import (
    evaluate_all_objectives,
    build_ranking_matrix,
    compute_kendall_tau_matrix,
    compute_objective_weights,
)
from plot_style import setup_style, save_figure, MCGS_CMAP


# ── Panel (a): Kendall tau heatmap ──────────────────────────────────────────

def _plot_tau_heatmap(
    ax: plt.Axes,
    tau_matrix: Dict[Tuple[str, str], float],
    objective_names: List[str],
):
    """Plot Kendall tau pairwise heatmap on given axes."""
    n = len(objective_names)
    matrix = np.zeros((n, n))
    for i, oi in enumerate(objective_names):
        matrix[i, i] = 1.0
        for j, oj in enumerate(objective_names):
            if i != j:
                matrix[i, j] = tau_matrix.get((oi, oj), 0.0)

    im = ax.imshow(matrix, cmap='RdBu_r', vmin=-0.5, vmax=1.0, aspect='equal')

    labels = [name[:12] for name in objective_names]
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=90, fontsize=max(6, 10 - n // 5))
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=max(6, 10 - n // 5))
    ax.set_xlabel("Objective")
    ax.set_ylabel("Objective")
    ax.set_title("(a) Kendall $\\tau$ Correlation", fontsize=14)

    return im


# ── Panel (b): Objective weights bar chart ──────────────────────────────────

def _plot_weights(
    ax: plt.Axes,
    weights: Dict[str, float],
    objective_names: List[str],
):
    """Plot objective weights as bar chart."""
    w = [weights.get(name, 0.0) for name in objective_names]
    n = len(objective_names)
    obj_norm = mcolors.Normalize(vmin=0, vmax=max(n - 1, 1))
    colors = [MCGS_CMAP(obj_norm(i)) for i in range(n)]

    ax.bar(range(n), w, color=colors, edgecolor='white', linewidth=0.5)
    ax.set_xlabel("Objective Index")
    ax.set_ylabel("Weight")
    ax.set_title("(b) Consensus Weights", fontsize=14)
    ax.set_ylim(bottom=0)
    ax.grid(axis='y', alpha=0.2)

    if n <= 20:
        ax.set_xticks(range(n))
        ax.set_xticklabels([name[:8] for name in objective_names],
                           rotation=45, ha='right', fontsize=max(6, 10 - n // 4))


# ── Panel (c): PCA embedding ───────────────────────────────────────────────

def _plot_pca(
    ax: plt.Axes,
    score_matrix: Dict[str, Dict[int, float]],
    weights: Dict[str, float],
    objective_names: List[str],
    node_ids: List[int],
):
    """Plot PCA 2D embedding of objectives based on their ranking vectors.

    Returns False if PCA cannot be computed (sklearn missing or <3 objectives).
    """
    n_obj = len(objective_names)
    if n_obj < 3:
        ax.text(0.5, 0.5, 'Need ≥3 objectives\nfor PCA',
                ha='center', va='center', transform=ax.transAxes, fontsize=12)
        ax.set_title("(c) Objective Embedding (PCA)", fontsize=14)
        ax.axis('off')
        return False

    try:
        from sklearn.decomposition import PCA
    except ImportError:
        ax.text(0.5, 0.5, 'scikit-learn not installed\n(pip install scikit-learn)',
                ha='center', va='center', transform=ax.transAxes, fontsize=12)
        ax.set_title("(c) Objective Embedding (PCA)", fontsize=14)
        ax.axis('off')
        return False

    # Build score matrix: (n_objectives x n_nodes)
    mat = np.full((n_obj, len(node_ids)), np.nan)
    for i, name in enumerate(objective_names):
        scores = score_matrix.get(name, {})
        for j, nid in enumerate(node_ids):
            mat[i, j] = scores.get(nid, np.nan)

    # Rank within each objective (row), NaN gets worst rank
    rank_mat = np.zeros_like(mat)
    for i in range(n_obj):
        row = mat[i]
        valid_mask = ~np.isnan(row)
        if valid_mask.any():
            order = np.argsort(row[valid_mask])
            ranks = np.empty_like(order, dtype=float)
            ranks[order] = np.arange(order.size, dtype=float)
            rank_mat[i, valid_mask] = ranks
            rank_mat[i, ~valid_mask] = order.size  # worst rank for NaN
        else:
            rank_mat[i, :] = 0

    # Standardize
    X = rank_mat
    X_centered = X - X.mean(axis=1, keepdims=True)
    std = X_centered.std(axis=1, keepdims=True)
    std[std < 1e-10] = 1.0
    X_std = X_centered / std

    # PCA
    pca = PCA(n_components=2)
    coords = pca.fit_transform(X_std)

    # Sizing by weight
    w_arr = np.array([weights.get(name, 0.0) for name in objective_names])
    w_max = w_arr.max() if w_arr.max() > 0 else 1.0
    sizes = 40 + (w_arr / w_max) * 360  # 40..400 points²

    obj_ids = np.arange(n_obj)
    obj_norm = mcolors.Normalize(vmin=0, vmax=max(n_obj - 1, 1))

    sc = ax.scatter(
        coords[:, 0], coords[:, 1],
        c=obj_ids, cmap=MCGS_CMAP, norm=obj_norm,
        s=sizes, edgecolors='white', linewidths=0.5, zorder=3,
    )

    # Label points
    import matplotlib.patheffects as pe
    outline = [pe.withStroke(linewidth=2.5, foreground='white')]
    for oid in range(n_obj):
        label = objective_names[oid][:8]
        ax.annotate(
            label, (coords[oid, 0], coords[oid, 1]),
            textcoords="offset points", xytext=(6, 6),
            fontsize=max(6, 9 - n_obj // 8), fontweight='bold',
            color='black', path_effects=outline,
        )

    ev = pca.explained_variance_ratio_
    ax.set_xlabel(f"PC1 ({ev[0]:.0%})")
    ax.set_ylabel(f"PC2 ({ev[1]:.0%})")
    ax.set_title("(c) Objective Embedding (PCA)", fontsize=14)

    return True


# ── Main entry point ────────────────────────────────────────────────────────

def plot_objective_analysis(
    graph: MCGSGraph,
    objectives_dir: Path,
    output_dir: Path,
):
    """Generate combined multi-panel objective analysis figure.

    Skips if single-objective mode or no objectives defined.
    """
    if not graph.config.multi_objective or not graph.objectives:
        print("Single-objective mode — skipping objective analysis plot.")
        return

    active = graph.get_active_objectives()
    if len(active) < 2:
        print("Need ≥2 active objectives — skipping objective analysis plot.")
        return

    # Compute consensus data
    score_matrix, objective_names, node_ids = evaluate_all_objectives(graph, objectives_dir)
    if not objective_names or not node_ids:
        print("No objectives or nodes to analyze — skipping.")
        return

    ranking_matrix = build_ranking_matrix(score_matrix)
    tau_matrix = compute_kendall_tau_matrix(ranking_matrix, objective_names)
    weights = compute_objective_weights(
        tau_matrix, objective_names, active,
        current_iteration=graph.total_iterations,
        age_decay=graph.config.age_decay,
    )

    # Determine layout: 2 or 3 panels
    n_obj = len(objective_names)
    has_pca = n_obj >= 3
    try:
        from sklearn.decomposition import PCA  # noqa: F401
    except ImportError:
        has_pca = False

    if has_pca:
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5),
                                 gridspec_kw={'width_ratios': [1, 0.8, 1]})
    else:
        fig, axes_raw = plt.subplots(1, 2, figsize=(14, 5.5))
        axes = list(axes_raw) + [None]

    # Panel (a): tau heatmap
    im = _plot_tau_heatmap(axes[0], tau_matrix, objective_names)
    fig.colorbar(im, ax=axes[0], shrink=0.85, pad=0.02, label="Kendall $\\tau$")

    # Panel (b): weights
    _plot_weights(axes[1], weights, objective_names)

    # Panel (c): PCA
    if has_pca and axes[2] is not None:
        _plot_pca(axes[2], score_matrix, weights, objective_names, node_ids)

    fig.tight_layout()
    save_figure(fig, output_dir, 'objective_analysis')


def main():
    parser = argparse.ArgumentParser(description="MCGS objective analysis")
    parser.add_argument("--graph", default="mcgs_graph.json")
    parser.add_argument("--objectives-dir", default=None)
    parser.add_argument("--output-dir", default="figures")
    args = parser.parse_args()

    setup_style()
    graph = load_graph(args.graph)

    obj_dir = Path(args.objectives_dir) if args.objectives_dir else Path(graph.config.objectives_dir)
    plot_objective_analysis(graph, obj_dir, Path(args.output_dir))


if __name__ == '__main__':
    main()
