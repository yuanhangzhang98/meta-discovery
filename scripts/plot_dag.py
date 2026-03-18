#!/usr/bin/env python3
"""DAG visualization for MCGS design genealogy.

Creates a publication-quality figure showing the directed acyclic graph
of design evolution, with edges weighted by parent_edges weights.

Default layout: graphviz (dot engine) — tested for large graphs.
Fallback: sugiyama (pure Python) if graphviz is not installed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import networkx as nx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, load_graph
from plot_style import setup_style, save_figure, COLOR_BEST, COLOR_BASELINE, COLOR_FAILED


# ============================================================================ #
# Data Loading
# ============================================================================ #

def build_graph(graph: MCGSGraph) -> nx.DiGraph:
    """Build networkx DiGraph from MCGSGraph.

    Node attributes: short_name, objective, status, ucb_score
    Edge attributes: weight (from parent_edges)
    Edges go parent -> child (direction of inheritance).
    """
    G = nx.DiGraph()

    for node in graph.nodes:
        obj = node.consensus_score if node.consensus_score is not None else node.objective
        G.add_node(
            node.id,
            short_name=node.short_name,
            objective=obj,
            status=node.status,
            ucb_score=node.ucb_score,
            fidelity_level=node.fidelity_level,
        )
        for edge in node.parent_edges:
            G.add_edge(edge.node_id, node.id, weight=edge.weight)

    return G


# ============================================================================ #
# Layout Algorithms
# ============================================================================ #

def graphviz_layout(
    G: nx.DiGraph,
    prog: str = 'dot',
    rankdir: str = 'TB',
) -> Optional[Dict[int, Tuple[float, float]]]:
    """Layout using Graphviz dot engine via pygraphviz or pydot.

    Returns None if graphviz is not available.
    """
    if G.number_of_nodes() == 0:
        return {}

    G_copy = G.copy()
    if prog == 'dot':
        G_copy.graph['rankdir'] = rankdir
        G_copy.graph['ranksep'] = '0.5'
        G_copy.graph['nodesep'] = '0.3'

    gv_args = f'-Grankdir={rankdir} -Granksep=0.5 -Gnodesep=0.3'

    # Try pygraphviz first, then pydot
    for importer in ['nx_agraph', 'nx_pydot']:
        try:
            if importer == 'nx_agraph':
                from networkx.drawing.nx_agraph import graphviz_layout as _gv_layout
                return _gv_layout(G_copy, prog=prog, args=gv_args)
            else:
                from networkx.drawing.nx_pydot import graphviz_layout as _gv_layout
                return _gv_layout(G_copy, prog=prog)
        except ImportError:
            continue
        except Exception as e:
            print(f"WARNING: graphviz {prog} failed: {e}")
            break

    return None  # signal caller to use fallback


def _compact_layers(
    G: nx.DiGraph,
    node_depth: Dict[int, int],
    iterations: int = 3,
) -> Dict[int, int]:
    """Compact sparse layers by moving nodes within their valid range."""
    min_layer: Dict[int, int] = {}
    max_layer: Dict[int, float] = {}

    for node in G.nodes():
        parents = list(G.predecessors(node))
        children = list(G.successors(node))
        min_layer[node] = (max(node_depth[p] for p in parents) + 1) if parents else 0
        max_layer[node] = (min(node_depth[c] for c in children) - 1) if children else float('inf')

    for _ in range(iterations):
        layer_sizes: Dict[int, int] = {}
        for depth in node_depth.values():
            layer_sizes[depth] = layer_sizes.get(depth, 0) + 1

        if not layer_sizes:
            break

        avg_size = sum(layer_sizes.values()) / len(layer_sizes)
        max_depth = max(node_depth.values())

        for node in list(G.nodes()):
            current = node_depth[node]
            current_size = layer_sizes.get(current, 0)
            if current_size >= avg_size:
                continue

            best_target = current
            best_improvement = 0
            valid_max = min(max_layer[node], max_depth)
            for target in range(min_layer[node], int(valid_max) + 1):
                if target == current:
                    continue
                target_size = layer_sizes.get(target, 0)
                improvement = current_size - target_size - 1
                if improvement > best_improvement:
                    best_improvement = improvement
                    best_target = target

            if best_target != current:
                layer_sizes[current] -= 1
                layer_sizes[best_target] = layer_sizes.get(best_target, 0) + 1
                node_depth[node] = best_target

    # Renumber to remove gaps
    used_depths = sorted(set(node_depth.values()))
    depth_map = {old: new for new, old in enumerate(used_depths)}
    return {node: depth_map[depth] for node, depth in node_depth.items()}


def sugiyama_layout(
    G: nx.DiGraph,
    horizontal_spacing: float = 1.0,
    vertical_spacing: float = 1.2,
    iterations: int = 4,
) -> Dict[int, Tuple[float, float]]:
    """Sugiyama-style layered layout for DAGs (fallback when graphviz unavailable).

    1. Layer assignment by longest path from root
    2. Edge crossing minimization via barycenter heuristic
    3. Coordinate assignment centered per layer
    """
    if G.number_of_nodes() == 0:
        return {}

    # Phase 1: Layer assignment
    node_depth: Dict[int, int] = {n: 0 for n in G.nodes()}
    try:
        topo_order = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        topo_order = list(G.nodes())

    for node in topo_order:
        predecessors = list(G.predecessors(node))
        if predecessors:
            node_depth[node] = max(node_depth[p] for p in predecessors) + 1

    node_depth = _compact_layers(G, node_depth, iterations=3)

    layers: Dict[int, List[int]] = {}
    for node, depth in node_depth.items():
        layers.setdefault(depth, []).append(node)

    sorted_depths = sorted(layers.keys())
    layer_list = [layers[d] for d in sorted_depths]
    for layer in layer_list:
        layer.sort()

    # Phase 2: Barycenter crossing minimization
    def get_layer_positions(ll):
        pil = {}
        for li, layer in enumerate(ll):
            for idx, node in enumerate(layer):
                pil[node] = (li, idx)
        return pil

    for _ in range(iterations):
        for layer_idx in range(1, len(layer_list)):
            pil = get_layer_positions(layer_list)
            bcs = []
            for node in layer_list[layer_idx]:
                parents = list(G.predecessors(node))
                if parents:
                    pp = [pil[p][1] for p in parents if p in pil]
                    bc = sum(pp) / len(pp) if pp else pil[node][1]
                else:
                    bc = pil[node][1]
                bcs.append((bc, node))
            bcs.sort(key=lambda x: x[0])
            layer_list[layer_idx] = [n for _, n in bcs]

        for layer_idx in range(len(layer_list) - 2, -1, -1):
            pil = get_layer_positions(layer_list)
            bcs = []
            for node in layer_list[layer_idx]:
                children = list(G.successors(node))
                if children:
                    cp = [pil[c][1] for c in children if c in pil]
                    bc = sum(cp) / len(cp) if cp else pil[node][1]
                else:
                    bc = pil[node][1]
                bcs.append((bc, node))
            bcs.sort(key=lambda x: x[0])
            layer_list[layer_idx] = [n for _, n in bcs]

    # Phase 3: Coordinate assignment
    pos = {}
    for layer_idx, layer in enumerate(layer_list):
        n_nodes = len(layer)
        x_offset = -(n_nodes - 1) * horizontal_spacing / 2
        for x_idx, node in enumerate(layer):
            pos[node] = (x_offset + x_idx * horizontal_spacing, -layer_idx * vertical_spacing)

    return pos


def select_layout(G: nx.DiGraph) -> Tuple[Dict[int, Tuple[float, float]], str]:
    """Try graphviz layout, fall back to sugiyama.

    Returns (positions, layout_name).
    """
    pos = graphviz_layout(G)
    if pos is not None:
        return pos, 'graphviz'

    print("Graphviz not available — using sugiyama fallback layout.")
    return sugiyama_layout(G), 'sugiyama'


# ============================================================================ #
# Styling helpers
# ============================================================================ #

def get_node_colors(
    G: nx.DiGraph,
    color_by: str = 'objective',
    minimize: bool = True,
) -> Tuple[List[float], Normalize, object]:
    """Compute node colors based on coloring scheme.

    color_by: 'node_id', 'objective', 'status'
    """
    nodes = list(G.nodes())
    cmap = plt.colormaps.get_cmap('viridis')

    if color_by == 'node_id':
        values = [float(n) for n in nodes]
        vmin, vmax = min(values), max(values) if values else (0, 1)
    elif color_by == 'objective':
        values = []
        for n in nodes:
            obj = G.nodes[n].get('objective')
            values.append(obj if obj is not None else float('nan'))
        valid = [v for v in values if not np.isnan(v)]
        if valid:
            vmin, vmax = min(valid), max(valid)
            if minimize:
                # Invert so lower (better) is brighter
                cmap = plt.colormaps.get_cmap('viridis_r')
        else:
            vmin, vmax = 0, 1
    elif color_by == 'status':
        status_map = {'evaluated': 0.8, 'pending': 0.5, 'failed': 0.1}
        values = [status_map.get(G.nodes[n].get('status', 'pending'), 0.5) for n in nodes]
        vmin, vmax = 0, 1
    else:
        raise ValueError(f"Unknown color_by: {color_by}")

    norm = Normalize(vmin=vmin, vmax=vmax)
    return values, norm, cmap


def get_edge_styles(
    G: nx.DiGraph,
    base_width: float = 0.5,
    max_width: float = 3.0,
    base_alpha: float = 0.3,
    max_alpha: float = 0.8,
) -> Tuple[List[float], List[float]]:
    """Compute edge widths and alphas based on weights."""
    widths, alphas = [], []
    for u, v, data in G.edges(data=True):
        w = data.get('weight', 1.0)
        widths.append(base_width + w * (max_width - base_width))
        alphas.append(base_alpha + w * (max_alpha - base_alpha))
    return widths, alphas


def get_node_sizes(
    G: nx.DiGraph,
    base_size: float = 800,
    minimize: bool = True,
) -> List[float]:
    """Compute node sizes proportional to objective quality."""
    objectives = {}
    for node in G.nodes():
        obj = G.nodes[node].get('objective')
        if obj is not None and not (isinstance(obj, float) and np.isnan(obj)):
            objectives[node] = obj

    if not objectives:
        return [base_size for _ in G.nodes()]

    obj_min = min(objectives.values())
    obj_max = max(objectives.values())

    sizes = []
    for node in G.nodes():
        obj = objectives.get(node)
        if obj is None:
            sizes.append(base_size * 0.15)
            continue

        # Normalize to [0, 1] where 1 = best
        if obj_max > obj_min:
            t = (obj - obj_min) / (obj_max - obj_min)
            if minimize:
                t = 1.0 - t  # lower is better
        else:
            t = 0.5

        multiplier = 0.15 + 4.85 * t
        sizes.append(base_size * multiplier)

    return sizes


def get_visible_labels(
    G: nx.DiGraph,
    max_labels: Optional[int] = None,
    always_show: Optional[List[int]] = None,
) -> Dict[int, str]:
    """Determine which labels to show. None max_labels = show all."""
    if always_show is None:
        always_show = [0]

    if max_labels is None or G.number_of_nodes() <= max_labels:
        return {n: str(n) for n in G.nodes()}

    priority = []
    for node in G.nodes():
        if node in always_show:
            priority.append((node, float('inf')))
        else:
            priority.append((node, G.out_degree(node)))
    priority.sort(key=lambda x: x[1], reverse=True)
    selected = [node for node, _ in priority[:max_labels]]
    return {n: str(n) for n in selected}


# ============================================================================ #
# Drawing
# ============================================================================ #

def draw_dag(
    G: nx.DiGraph,
    pos: Dict[int, Tuple[float, float]],
    ax: plt.Axes,
    fig: Optional[plt.Figure] = None,
    color_by: str = 'objective',
    minimize: bool = True,
    best_id: Optional[int] = None,
    show_labels: bool = True,
    show_colorbar: bool = True,
) -> None:
    """Draw the DAG on matplotlib axes."""
    nodes = list(G.nodes())
    node_set = set(nodes)
    n_nodes = G.number_of_nodes()

    # Auto-scale for dense graphs
    if n_nodes > 200:
        base_size = max(80, 800 - n_nodes)
        label_fontsize = max(4, 12 - n_nodes // 50)
    elif n_nodes > 100:
        base_size = max(300, 800 - n_nodes * 2)
        label_fontsize = max(6, 14 - n_nodes // 30)
    else:
        base_size = 800
        label_fontsize = 14

    # Styling
    color_values, norm, cmap = get_node_colors(G, color_by, minimize)
    widths, alphas = get_edge_styles(G)
    sizes = get_node_sizes(G, base_size=base_size, minimize=minimize)

    best_set = {best_id} & node_set if best_id is not None else set()
    baseline_set = {0} & node_set

    # Draw edges with curved arrows for multi-parent relationships
    edge_color = '#505050'
    for (u, v, data), width, alpha in zip(G.edges(data=True), widths, alphas):
        n_parents = G.in_degree(v)
        parent_list = list(G.predecessors(v))
        if n_parents > 1 and u in parent_list:
            idx = parent_list.index(u)
            rad = 0.15 * (idx - (n_parents - 1) / 2)
            style = f'arc3,rad={rad}'
        else:
            style = 'arc3,rad=0'

        arrow = FancyArrowPatch(
            pos[u], pos[v],
            arrowstyle='-|>',
            connectionstyle=style,
            linewidth=width,
            alpha=alpha,
            color=edge_color,
            mutation_scale=12,
            zorder=1,
        )
        ax.add_patch(arrow)

    # Prepare node colors
    node_colors = []
    for i, node in enumerate(nodes):
        val = color_values[i]
        if isinstance(val, float) and np.isnan(val):
            node_colors.append('#cccccc')
        else:
            node_colors.append(cmap(norm(val)))

    special_nodes = baseline_set | best_set

    # Draw regular nodes
    regular_nodes = [n for n in nodes if n not in special_nodes]
    if regular_nodes:
        ax.scatter(
            [pos[n][0] for n in regular_nodes],
            [pos[n][1] for n in regular_nodes],
            c=[node_colors[nodes.index(n)] for n in regular_nodes],
            s=[sizes[nodes.index(n)] for n in regular_nodes],
            marker='o',
            edgecolors='white',
            linewidths=0.8,
            zorder=2,
        )

    # Draw baseline (node 0)
    if 0 in node_set:
        ax.scatter(
            pos[0][0], pos[0][1],
            c=COLOR_BASELINE,
            s=base_size * 1.8,
            marker='o',
            edgecolors='white',
            linewidths=1.0,
            zorder=3,
        )

    # Draw best node
    if best_id is not None and best_id in node_set:
        ax.scatter(
            pos[best_id][0], pos[best_id][1],
            c=COLOR_BEST,
            s=sizes[nodes.index(best_id)],
            marker='D',
            edgecolors='white',
            linewidths=1.0,
            zorder=4,
        )

    # Label special nodes
    sp_fontsize = max(label_fontsize, 6)
    if best_id is not None and best_id in node_set:
        ax.annotate(str(best_id), pos[best_id], fontsize=sp_fontsize,
                    ha='center', va='center', color='white',
                    fontweight='bold', zorder=5)
    if 0 in node_set:
        ax.annotate('B', pos[0], fontsize=sp_fontsize,
                    ha='center', va='center', color='white',
                    fontweight='bold', zorder=5)

    # Other labels
    if show_labels:
        labels = get_visible_labels(G, always_show=list(special_nodes))
        for node, label in labels.items():
            if node in special_nodes:
                continue
            x, y = pos[node]
            idx = nodes.index(node)
            val = color_values[idx]
            nval = norm(val) if not (isinstance(val, float) and np.isnan(val)) else 0.5
            label_color = 'black' if nval < 0.5 else 'white'
            ax.annotate(
                label, (x, y),
                fontsize=label_fontsize,
                ha='center', va='center',
                color=label_color, fontweight='bold',
                zorder=4,
            )

    # Colorbar
    if show_colorbar and fig is not None:
        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cbar_label = {
            'objective': 'Objective',
            'node_id': 'Node ID',
            'status': 'Status',
        }.get(color_by, color_by)
        cbar = fig.colorbar(sm, ax=ax, pad=0.02, aspect=30, shrink=1.0)
        cbar.set_label(cbar_label, fontsize=18)
        cbar.ax.tick_params(labelsize=14)

    ax.axis('off')

    # Legend
    legend_elements = []
    if 0 in node_set:
        legend_elements.append(
            Line2D([0], [0], marker='o', color='w', markerfacecolor=COLOR_BASELINE,
                   markersize=10, label='Baseline'))
    if best_id is not None and best_id in node_set:
        legend_elements.append(
            Line2D([0], [0], marker='D', color='w', markerfacecolor=COLOR_BEST,
                   markersize=10, label=f'Best (node {best_id})'))
    if legend_elements:
        ax.legend(
            handles=legend_elements, loc='upper right',
            frameon=True, framealpha=0.95, edgecolor='none',
            handletextpad=0.3, labelspacing=0.3, fontsize=14,
        )


def compute_figure_size(
    G: nx.DiGraph,
    pos: Dict[int, Tuple[float, float]],
    layout_name: str = 'graphviz',
) -> Tuple[float, float]:
    """Compute appropriate figure size based on graph and layout."""
    n_nodes = G.number_of_nodes()

    if not pos:
        width = max(10, min(18, np.sqrt(n_nodes) * 1.5 + 4))
        return (width, width * 0.7)

    x_coords = [p[0] for p in pos.values()]
    y_coords = [p[1] for p in pos.values()]
    x_range = max(x_coords) - min(x_coords) if x_coords else 1
    y_range = max(y_coords) - min(y_coords) if y_coords else 1

    if layout_name == 'graphviz':
        aspect = x_range / max(y_range, 1)
        if aspect > 1:
            width = min(20, max(14, x_range * 0.004 + 4))
            height = width / aspect
        else:
            height = min(20, max(14, y_range * 0.004 + 4))
            width = height * aspect
        return (max(width, 10), max(height, 8))
    else:
        width = max(10, min(20, x_range * 1.2 + 4))
        height = max(8, min(16, y_range * 1.2 + 4))
        return (width, height)


# ============================================================================ #
# Main entry point
# ============================================================================ #

def plot_dag(graph: MCGSGraph, output_dir: Path, color_by: str = 'objective'):
    """Generate DAG visualization from MCGSGraph."""
    G = build_graph(graph)

    if G.number_of_nodes() == 0:
        print("No nodes in graph — skipping DAG plot.")
        return

    pos, layout_name = select_layout(G)

    if not pos:
        print("Layout produced no positions — skipping DAG plot.")
        return

    # Find best node
    minimize = graph.config.minimize
    evaluated = [n for n in graph.nodes if n.objective is not None]
    best_id = None
    if evaluated:
        if minimize:
            best_node = min(evaluated, key=lambda n: n.objective)
        else:
            best_node = max(evaluated, key=lambda n: n.objective)
        best_id = best_node.id

    fig_size = compute_figure_size(G, pos, layout_name)
    fig, ax = plt.subplots(figsize=fig_size)

    draw_dag(
        G, pos, ax,
        fig=fig,
        color_by=color_by,
        minimize=minimize,
        best_id=best_id,
    )

    save_figure(fig, output_dir, 'dag')


def main():
    parser = argparse.ArgumentParser(description="MCGS DAG visualization")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--output-dir", default="figures", help="Output directory")
    parser.add_argument("--color-by", default="objective",
                        choices=["objective", "node_id", "status"])
    args = parser.parse_args()

    setup_style()
    graph = load_graph(args.graph)
    plot_dag(graph, Path(args.output_dir), color_by=args.color_by)


if __name__ == '__main__':
    main()
