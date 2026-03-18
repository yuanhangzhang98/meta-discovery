#!/usr/bin/env python3
"""Pre-generate figures and extract data summaries for the MCGS research report.

This script is a *preparation* step — it generates the standard figures and
prints a data summary. The actual LaTeX report is written by the orchestrating
agent, who has full control over structure, narrative, and additional figures.

Usage:
    python generate_report.py --graph mcgs_graph.json --output-dir mcgs_report/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import MCGSGraph, GraphNode, load_graph
from plot_style import setup_style


# ── LaTeX helpers (importable by the agent for convenience) ──────────────────

def escape_latex(text: str) -> str:
    """Escape LaTeX special characters."""
    replacements = [
        ('\\', r'\textbackslash{}'),
        ('&', r'\&'),
        ('%', r'\%'),
        ('$', r'\$'),
        ('#', r'\#'),
        ('_', r'\_'),
        ('{', r'\{'),
        ('}', r'\}'),
        ('~', r'\textasciitilde{}'),
        ('^', r'\textasciicircum{}'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def build_lineage(graph: MCGSGraph, node_id: int) -> List[int]:
    """Trace primary parent chain from node_id back to node-0."""
    chain = [node_id]
    visited = {node_id}
    current = node_id
    while True:
        node = graph.get_node(current)
        if node is None or not node.parent_edges:
            break
        best_parent = max(node.parent_edges, key=lambda e: e.weight)
        if best_parent.node_id in visited:
            break
        visited.add(best_parent.node_id)
        chain.append(best_parent.node_id)
        current = best_parent.node_id
    chain.reverse()
    return chain


def compile_pdf(tex_path: Path):
    """Compile LaTeX to PDF using pdflatex (2 passes for references)."""
    try:
        for _ in range(2):
            result = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-halt-on-error',
                 tex_path.name],
                cwd=str(tex_path.parent),
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"pdflatex warning (may still produce PDF):\n{result.stdout[-500:]}")

        pdf_path = tex_path.with_suffix('.pdf')
        if pdf_path.exists():
            print(f"PDF compiled: {pdf_path}")
        else:
            print("PDF compilation may have failed. Check the .log file for details.")
    except FileNotFoundError:
        print("pdflatex not found — skipping PDF compilation.")
        print(f"You can compile manually: pdflatex {tex_path}")
    except subprocess.TimeoutExpired:
        print("pdflatex timed out after 120s.")


# ── Figure generation ────────────────────────────────────────────────────────

def generate_figures(graph: MCGSGraph, figures_dir: Path, objectives_dir: Optional[Path]):
    """Generate all standard figures by calling plotting modules."""
    from plot_convergence import plot_convergence, plot_node_comparison
    from plot_dag import plot_dag

    plot_convergence(graph, figures_dir)
    plot_node_comparison(graph, figures_dir)
    plot_dag(graph, figures_dir)

    if graph.config.multi_objective and graph.objectives and objectives_dir:
        from plot_objectives import plot_objective_analysis
        plot_objective_analysis(graph, objectives_dir, figures_dir)


# ── Data summary (printed for the agent to use) ─────────────────────────────

def _get_best_node(graph: MCGSGraph) -> Optional[GraphNode]:
    """Get the best evaluated node."""
    evaluated = [n for n in graph.nodes if n.objective is not None]
    if not evaluated:
        return None
    if graph.config.minimize:
        return min(evaluated, key=lambda n: n.objective)
    return max(evaluated, key=lambda n: n.objective)


def build_data_summary(graph: MCGSGraph) -> dict:
    """Build a structured data summary for the agent to use when writing the report."""
    best = _get_best_node(graph)
    baseline = graph.get_node(0)
    evaluated = [n for n in graph.nodes if n.objective is not None]
    failed = [n for n in graph.nodes if n.status == 'failed']

    # Improvement calculation
    improvement = None
    if best and baseline and baseline.objective is not None and best.objective is not None:
        if baseline.objective != 0:
            if graph.config.minimize:
                diff = baseline.objective - best.objective
            else:
                diff = best.objective - baseline.objective
            improvement = {
                "absolute": diff,
                "percentage": (diff / abs(baseline.objective)) * 100,
                "direction": "improvement" if diff > 0 else "regression",
            }

    # Top designs
    top_designs = sorted(evaluated, key=lambda n: n.objective,
                         reverse=not graph.config.minimize)[:10]

    summary = {
        "research_goal": graph.config.research_goal,
        "optimization": "minimize" if graph.config.minimize else "maximize",
        "mode": "multi-objective" if graph.config.multi_objective else "single-objective",
        "total_nodes": len(graph.nodes),
        "evaluated": len(evaluated),
        "failed": len(failed),
        "total_iterations": graph.total_iterations,
        "best_node": {
            "id": best.id,
            "name": best.short_name,
            "objective": best.objective,
            "description": best.description,
            "lineage": build_lineage(graph, best.id),
        } if best else None,
        "baseline_objective": baseline.objective if baseline else None,
        "improvement": improvement,
        "top_designs": [
            {"id": n.id, "name": n.short_name, "objective": n.objective,
             "description": n.description, "status": n.status}
            for n in top_designs
        ],
        "lessons_learned": graph.lessons_learned,
    }

    # Multi-objective info
    if graph.config.multi_objective and graph.objectives:
        active = graph.get_active_objectives()
        summary["objectives"] = {
            "total": len(graph.objectives),
            "active": len(active),
            "names": [o.name for o in active],
            "descriptions": {o.name: o.description for o in active},
        }
        if graph.meta_state:
            summary["meta_state"] = {
                "current_phase": graph.meta_state.research_phase,
                "assessment": graph.meta_state.research_assessment,
                "history": [
                    {"phase": s.get("research_phase", "?"),
                     "assessment": s.get("research_assessment", ""),
                     "timestamp": s.get("timestamp", "")}
                    for s in graph.meta_state.history
                ],
            }

    # Config for appendix
    cfg = graph.config
    summary["config"] = {
        "c_puct": cfg.c_puct,
        "decay_factor": cfg.decay_factor,
        "multi_fidelity": cfg.multi_fidelity,
    }
    if cfg.multi_fidelity:
        summary["config"]["fidelity_tiers"] = [t["name"] for t in cfg.fidelity_tiers]

    return summary


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pre-generate figures and data summary for MCGS research report")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--output-dir", default="mcgs_report", help="Report output directory")
    parser.add_argument("--objectives-dir", default=None,
                        help="Objectives directory (default: from graph config)")
    args = parser.parse_args()

    setup_style()
    graph = load_graph(args.graph)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / 'figures'
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Resolve objectives directory
    objectives_dir = None
    if args.objectives_dir:
        objectives_dir = Path(args.objectives_dir)
    elif graph.config.multi_objective:
        objectives_dir = Path(graph.config.objectives_dir)

    # Generate figures
    print("Generating figures...")
    generate_figures(graph, figures_dir, objectives_dir)

    # Build and write data summary
    print("\nBuilding data summary...")
    summary = build_data_summary(graph)
    summary_path = output_dir / 'data_summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Data summary written to {summary_path}")

    # Print summary for agent context
    best = summary.get("best_node")
    imp = summary.get("improvement")
    print(f"\n{'='*60}")
    print(f"REPORT DATA READY")
    print(f"{'='*60}")
    print(f"Research goal: {summary['research_goal']}")
    print(f"Mode: {summary['mode']}, {summary['optimization']}")
    print(f"Nodes: {summary['total_nodes']} total, {summary['evaluated']} evaluated, {summary['failed']} failed")
    print(f"Iterations: {summary['total_iterations']}")
    if best:
        print(f"Best: node {best['id']} ({best['name']}), objective = {best['objective']}")
        print(f"Lineage: {' -> '.join(str(x) for x in best['lineage'])}")
    if imp:
        print(f"Improvement: {imp['percentage']:.1f}% {imp['direction']} over baseline")
    print(f"\nFigures: {figures_dir}/")
    print(f"Data summary: {summary_path}")
    print(f"\nThe agent should now write the LaTeX report in {output_dir}/")


if __name__ == '__main__':
    main()
