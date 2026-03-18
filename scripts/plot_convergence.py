#!/usr/bin/env python3
"""Convergence and node comparison plots for MCGS.

Generates:
- Convergence curve: best-so-far objective over node creation order
- Node comparison: horizontal bar chart of top-N evaluated nodes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, load_graph
from plot_style import setup_style, save_figure, COLOR_BEST, COLOR_BASELINE, COLOR_EVALUATED, COLOR_FAILED


def _get_objective(node):
    """Get the effective objective for a node (consensus if available)."""
    if node.consensus_score is not None:
        return node.consensus_score
    return node.objective


def plot_convergence(graph: MCGSGraph, output_dir: Path):
    """Plot best-so-far objective over node creation order."""
    minimize = graph.config.minimize

    # Collect evaluated nodes in creation order
    nodes = sorted(
        [n for n in graph.nodes if _get_objective(n) is not None],
        key=lambda n: n.id,
    )
    if not nodes:
        print("No evaluated nodes — skipping convergence plot.")
        return

    ids = [n.id for n in nodes]
    objectives = [_get_objective(n) for n in nodes]

    # Compute running best
    running_best = []
    best_so_far = None
    for obj in objectives:
        if best_so_far is None:
            best_so_far = obj
        elif minimize and obj < best_so_far:
            best_so_far = obj
        elif not minimize and obj > best_so_far:
            best_so_far = obj
        running_best.append(best_so_far)

    best_idx = (np.argmin(objectives) if minimize else np.argmax(objectives))

    fig, ax = plt.subplots(figsize=(8, 5))

    # Individual node objectives
    ax.scatter(ids, objectives, c=COLOR_EVALUATED, s=30, alpha=0.6,
               zorder=2, label='Node objective')

    # Running best line
    ax.plot(ids, running_best, color=COLOR_BEST, linewidth=2, zorder=3,
            label='Best so far')

    # Mark best node
    ax.scatter([ids[best_idx]], [objectives[best_idx]], c=COLOR_BEST,
               s=120, marker='D', zorder=4, edgecolors='black', linewidths=0.8,
               label=f'Best (node {ids[best_idx]})')

    # Mark baseline (node 0) if present
    baseline = graph.get_node(0)
    if baseline and _get_objective(baseline) is not None:
        ax.axhline(_get_objective(baseline), color=COLOR_BASELINE, linestyle='--',
                   linewidth=1, alpha=0.7, label='Baseline (node 0)')

    ax.set_xlabel('Node ID')
    ax.set_ylabel('Objective' + (' (lower is better)' if minimize else ' (higher is better)'))
    ax.legend(fontsize=11, loc='best')
    ax.grid(alpha=0.15)

    save_figure(fig, output_dir, 'convergence')


def plot_node_comparison(graph: MCGSGraph, output_dir: Path, top_n: int = 20):
    """Horizontal bar chart of top-N evaluated nodes sorted by objective."""
    minimize = graph.config.minimize

    evaluated = [n for n in graph.nodes if _get_objective(n) is not None]
    if not evaluated:
        print("No evaluated nodes — skipping node comparison plot.")
        return

    # Sort: best first
    evaluated.sort(key=lambda n: _get_objective(n), reverse=not minimize)
    show = evaluated[:top_n]
    show.reverse()  # bottom-to-top for horizontal bars

    labels = [f"{n.id}: {n.short_name[:30]}" for n in show]
    values = [_get_objective(n) for n in show]

    # Color by status
    colors = []
    best_id = evaluated[0].id
    for n in show:
        if n.id == best_id:
            colors.append(COLOR_BEST)
        elif n.id == 0:
            colors.append(COLOR_BASELINE)
        elif n.status == 'failed':
            colors.append(COLOR_FAILED)
        else:
            colors.append(COLOR_EVALUATED)

    fig_height = max(3, len(show) * 0.35 + 1)
    fig, ax = plt.subplots(figsize=(8, fig_height))

    ax.barh(range(len(show)), values, color=colors, edgecolor='white', linewidth=0.5)
    ax.set_yticks(range(len(show)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel('Objective' + (' (lower is better)' if minimize else ' (higher is better)'))
    ax.grid(axis='x', alpha=0.15)

    save_figure(fig, output_dir, 'node_comparison')


def main():
    parser = argparse.ArgumentParser(description="MCGS convergence plots")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--output-dir", default="figures", help="Output directory")
    parser.add_argument("--top-n", type=int, default=20, help="Top N nodes for comparison")
    args = parser.parse_args()

    setup_style()
    graph = load_graph(args.graph)
    output_dir = Path(args.output_dir)

    plot_convergence(graph, output_dir)
    plot_node_comparison(graph, output_dir, top_n=args.top_n)


if __name__ == '__main__':
    main()
