#!/usr/bin/env python3
"""Hyperparameter optimization for MCGS designs.

Tunes hyperparameters declared via a HYPER_SPACE dict in the design's source code.
Supports pluggable backends (Optuna default, HEBO optional).

HYPER_SPACE convention (in the design's source file):
    HYPER_SPACE = {
        "learning_rate": dict(type="log_uniform", default=0.001, low=1e-5, high=0.1),
        "momentum": dict(type="uniform", default=0.9, low=0.0, high=0.99),
        "hidden_dim": dict(type="int", default=128, low=32, high=512),
        "activation": dict(type="categorical", default="relu", choices=["relu", "gelu", "silu"]),
    }

Usage:
    python hpo_tune.py \
        --graph mcgs_graph.json \
        --node-id 5 \
        --repo-dir . \
        --max-iter 50 \
        --register
"""

from __future__ import annotations

import abc
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from graph_utils import (
    ParentEdge, load_graph, save_graph,
    _run_git, git_create_worktree, git_remove_worktree, git_commit_all,
)


# ──────────────────────────────────────────────────────────────────────────────
# HYPER_SPACE extraction and injection
# ──────────────────────────────────────────────────────────────────────────────

def find_hyper_space_file(worktree_path: Path, hint_file: str = "") -> Optional[Path]:
    """Find the file containing HYPER_SPACE in a worktree.

    If hint_file is provided, check it first. Otherwise, search all .py files.
    """
    if hint_file:
        candidate = worktree_path / hint_file
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if "HYPER_SPACE" in content:
                return candidate

    # Search all Python files
    for py_file in sorted(worktree_path.rglob("*.py")):
        # Skip common non-design files
        rel = py_file.relative_to(worktree_path)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if rel.name.startswith("mcgs_") or rel.name in ("evaluate.py", "run_experiment.py"):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            if "HYPER_SPACE" in content:
                return py_file
        except (UnicodeDecodeError, PermissionError):
            continue

    return None


def extract_hyper_space(source_code: str, component_name: str = "HYPER_SPACE") -> Dict[str, Dict[str, Any]]:
    """Extract HYPER_SPACE dict from source code using AST parsing.

    Returns dict of param_name -> {type, default, low, high, choices}.
    """
    tree = ast.parse(source_code)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == component_name:
                    # Evaluate the dict literal
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, dict):
                            return value
                    except (ValueError, TypeError):
                        pass
                    # If literal_eval fails, try compile+exec on just this assignment
                    try:
                        line_start = node.lineno - 1
                        line_end = node.end_lineno
                        lines = source_code.split("\n")
                        snippet = "\n".join(lines[line_start:line_end])
                        ns: Dict[str, Any] = {}
                        exec(compile(snippet, "<hyper_space>", "exec"), ns)  # noqa: S102
                        if component_name in ns and isinstance(ns[component_name], dict):
                            return ns[component_name]
                    except Exception:
                        pass

    raise ValueError(f"{component_name} not found in source code")


def inject_params(
    source_code: str,
    params: Dict[str, Any],
    component_name: str = "HYPER_SPACE",
) -> str:
    """Update default values in HYPER_SPACE via regex.

    For each param, finds its entry and replaces the default value.
    """
    for param_name, new_value in params.items():
        # Pattern: "param_name": dict(...default=OLD_VALUE...)
        pattern = rf'("{param_name}":\s*dict\([^)]*\bdefault\s*=\s*)([^,\)]+)'

        if isinstance(new_value, float):
            value_str = repr(new_value)
        elif isinstance(new_value, int):
            value_str = str(new_value)
        elif isinstance(new_value, str):
            value_str = f'"{new_value}"'
        elif isinstance(new_value, bool):
            value_str = str(new_value)
        else:
            value_str = repr(new_value)

        source_code = re.sub(pattern, rf"\g<1>{value_str}", source_code)

    return source_code


def get_defaults(hyper_space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Extract default values from HYPER_SPACE."""
    return {name: cfg["default"] for name, cfg in hyper_space.items() if "default" in cfg}


# ──────────────────────────────────────────────────────────────────────────────
# Backend abstraction
# ──────────────────────────────────────────────────────────────────────────────

class HPOBackend(abc.ABC):
    """Abstract HPO backend."""

    @abc.abstractmethod
    def create_study(self, hyper_space: Dict[str, Dict[str, Any]], minimize: bool = True) -> Any:
        ...

    @abc.abstractmethod
    def suggest(self, study: Any) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    def observe(self, study: Any, params: Dict[str, Any], metric: float) -> None:
        ...


class OptunaBackend(HPOBackend):
    """Optuna-based HPO backend using TPE sampler."""

    def create_study(self, hyper_space: Dict[str, Dict[str, Any]], minimize: bool = True) -> Any:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        direction = "minimize" if minimize else "maximize"
        study = optuna.create_study(direction=direction)

        # Store space config for suggest()
        study._hyper_space = hyper_space  # type: ignore[attr-defined]
        study._trial_params = {}  # type: ignore[attr-defined]
        return study

    def suggest(self, study: Any) -> Dict[str, Any]:
        import optuna
        hyper_space = study._hyper_space  # type: ignore[attr-defined]

        # Create a trial
        trial = study.ask()
        params: Dict[str, Any] = {}

        for name, cfg in hyper_space.items():
            hp_type = cfg.get("type", "uniform")
            if hp_type in ("uniform", "num"):
                params[name] = trial.suggest_float(name, cfg["low"], cfg["high"])
            elif hp_type == "log_uniform":
                params[name] = trial.suggest_float(name, cfg["low"], cfg["high"], log=True)
            elif hp_type in ("int", "integer"):
                params[name] = trial.suggest_int(name, cfg["low"], cfg["high"])
            elif hp_type == "categorical":
                choices = cfg.get("choices") or cfg.get("categories", [])
                params[name] = trial.suggest_categorical(name, choices)
            elif hp_type == "bool":
                params[name] = trial.suggest_categorical(name, [True, False])
            else:
                # Fallback: use default
                params[name] = cfg.get("default", 0)

        # Store trial for observe()
        study._trial_params[id(trial)] = trial  # type: ignore[attr-defined]
        study._current_trial = trial  # type: ignore[attr-defined]
        return params

    def observe(self, study: Any, params: Dict[str, Any], metric: float) -> None:
        trial = study._current_trial  # type: ignore[attr-defined]
        study.tell(trial, metric)


class HEBOBackend(HPOBackend):
    """HEBO-based HPO backend. Only available if hebo is installed."""

    def create_study(self, hyper_space: Dict[str, Dict[str, Any]], minimize: bool = True) -> Any:
        from hebo.design_space.design_space import DesignSpace
        from hebo.optimizers.hebo import HEBO

        space_cfg: List[Dict[str, Any]] = []
        for name, cfg in hyper_space.items():
            entry: Dict[str, Any] = {"name": name}
            hp_type = cfg.get("type", "uniform")
            if hp_type in ("uniform", "num"):
                entry.update({"type": "num", "lb": float(cfg["low"]), "ub": float(cfg["high"])})
            elif hp_type == "log_uniform":
                entry.update({"type": "pow", "lb": float(cfg["low"]), "ub": float(cfg["high"])})
            elif hp_type in ("int", "integer"):
                entry.update({"type": "int", "lb": int(cfg["low"]), "ub": int(cfg["high"])})
            elif hp_type == "categorical":
                entry.update({"type": "cat", "categories": list(cfg.get("choices") or cfg.get("categories"))})
            elif hp_type == "bool":
                entry.update({"type": "bool"})
            space_cfg.append(entry)

        space = DesignSpace().parse(space_cfg)
        optimizer = HEBO(space)
        return {"optimizer": optimizer, "space": hyper_space, "minimize": minimize}

    def suggest(self, study: Any) -> Dict[str, Any]:
        import pandas as pd
        optimizer = study["optimizer"]
        space = study["space"]
        suggestion_df = optimizer.suggest(n_suggestions=1)
        row = suggestion_df.iloc[0]
        params: Dict[str, Any] = {}
        for name, cfg in space.items():
            hp_type = cfg.get("type", "uniform")
            value = row[name]
            if hp_type in ("uniform", "num", "log_uniform"):
                params[name] = float(value)
            elif hp_type in ("int", "integer"):
                params[name] = int(round(value))
            else:
                params[name] = value
        study["_last_suggestion"] = suggestion_df
        return params

    def observe(self, study: Any, params: Dict[str, Any], metric: float) -> None:
        import numpy as np
        optimizer = study["optimizer"]
        suggestion_df = study["_last_suggestion"]
        objective = np.array([[metric]])
        optimizer.observe(suggestion_df, objective)


def get_backend(name: str) -> HPOBackend:
    """Get an HPO backend by name."""
    if name == "optuna":
        return OptunaBackend()
    elif name == "hebo":
        return HEBOBackend()
    else:
        raise ValueError(f"Unknown HPO backend: {name}. Supported: optuna, hebo")


# ──────────────────────────────────────────────────────────────────────────────
# Core tuning logic
# ──────────────────────────────────────────────────────────────────────────────

def _run_experiment(
    worktree_path: Path,
    script_name: str,
    timeout: int,
    fidelity_env: Dict[str, str] | None = None,
) -> float | None:
    """Run the experiment script and parse the metric from stdout."""
    run_env = {**os.environ}
    if fidelity_env:
        run_env.update(fidelity_env)

    try:
        result = subprocess.run(
            [sys.executable, script_name],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    # Parse last line
    lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    if not lines:
        return None

    last_line = lines[-1]

    # Try JSON (multi-objective: use first numeric value or a known key)
    try:
        data = json.loads(last_line)
        if isinstance(data, dict):
            # Use the first numeric value as metric (or 'loss' if present)
            for key in ("loss", "objective", "metric", "score"):
                if key in data:
                    return float(data[key])
            # Fallback: first numeric value
            for v in data.values():
                if isinstance(v, (int, float)):
                    return float(v)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try float
    try:
        return float(last_line)
    except ValueError:
        return None


def tune_node(
    graph_path: str,
    node_id: int,
    repo_dir: str = ".",
    max_iter: int = 50,
    backend_name: str = "optuna",
    timeout: int = 300,
    hyper_space_file: str = "",
) -> Dict[str, Any]:
    """Run HPO on a node's design.

    Args:
        graph_path: Path to mcgs_graph.json
        node_id: Node to tune
        repo_dir: Git repo directory
        max_iter: Max optimization iterations
        backend_name: HPO backend ("optuna" or "hebo")
        timeout: Timeout per experiment run
        hyper_space_file: Hint for which file contains HYPER_SPACE

    Returns:
        Summary dict with best_params, best_metric, history, hyper_space_path.
    """
    graph = load_graph(graph_path)
    node = graph.get_node(node_id)
    if node is None:
        return {"error": f"Node {node_id} not found"}

    # Determine experiment script
    multi_obj = graph.config.multi_objective
    script_name = graph.config.experiment_script if multi_obj else graph.config.objective_script

    # Create worktree
    repo_dir_path = Path(repo_dir).resolve()
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"mcgs-hpo-{node_id}-"))

    try:
        _run_git(["worktree", "add", str(worktree_dir), node.branch], cwd=repo_dir_path)

        # Find HYPER_SPACE file
        hs_file = find_hyper_space_file(worktree_dir, hyper_space_file or graph.config.hyper_space_file)
        if hs_file is None:
            return {"error": "No HYPER_SPACE found in node's code"}

        source_code = hs_file.read_text(encoding="utf-8")
        hyper_space = extract_hyper_space(source_code)
        if not hyper_space:
            return {"error": "HYPER_SPACE is empty"}

        hs_rel_path = str(hs_file.relative_to(worktree_dir))
        print(f"[HPO] Found HYPER_SPACE in {hs_rel_path} with {len(hyper_space)} params")
        print(f"[HPO] Params: {list(hyper_space.keys())}")

        # Get parent's metric as baseline
        parent_metric = None
        if node.objective is not None:
            parent_metric = node.objective

        # Create backend and study
        backend = get_backend(backend_name)
        study = backend.create_study(hyper_space, minimize=graph.config.minimize)

        # Warm-start with default params
        if parent_metric is not None:
            defaults = get_defaults(hyper_space)
            backend.observe(study, defaults, parent_metric)
            print(f"[HPO] Warm-started with parent metric: {parent_metric:.4f}")

        # Optimization loop
        best_metric = float("inf") if graph.config.minimize else float("-inf")
        best_params: Dict[str, Any] = {}
        history: List[Dict[str, Any]] = []

        for iteration in range(max_iter):
            params = backend.suggest(study)

            # Inject params into source code
            modified_code = inject_params(source_code, params)
            hs_file.write_text(modified_code, encoding="utf-8")

            # Run experiment
            metric = _run_experiment(worktree_dir, script_name, timeout)

            if metric is None:
                metric = float("inf") if graph.config.minimize else float("-inf")
                print(f"  Iter {iteration + 1}/{max_iter}: FAILED")
            else:
                print(f"  Iter {iteration + 1}/{max_iter}: metric={metric:.6f} params={params}")

            backend.observe(study, params, metric)
            history.append({"iteration": iteration, "params": params, "metric": metric})

            is_better = (metric < best_metric) if graph.config.minimize else (metric > best_metric)
            if is_better:
                best_metric = metric
                best_params = dict(params)
                print(f"  New best! metric={best_metric:.6f}")

        # Restore original source code
        hs_file.write_text(source_code, encoding="utf-8")

        return {
            "node_id": node_id,
            "best_params": best_params,
            "best_metric": best_metric,
            "parent_metric": parent_metric,
            "history": history,
            "hyper_space_file": hs_rel_path,
            "backend": backend_name,
            "iterations": max_iter,
        }

    finally:
        # Clean up worktree
        try:
            _run_git(["worktree", "remove", str(worktree_dir), "--force"], cwd=repo_dir_path, check=False)
        except Exception:
            pass
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)


def register_tuned_node(
    graph_path: str,
    parent_node_id: int,
    best_params: Dict[str, Any],
    repo_dir: str = ".",
    hyper_space_file: str = "",
) -> int:
    """Create a new graph node with tuned hyperparameters.

    Creates a git branch from the parent, injects best params, commits,
    and registers the node in the graph.

    Returns the new node ID.
    """
    graph = load_graph(graph_path)
    parent = graph.get_node(parent_node_id)
    if parent is None:
        raise ValueError(f"Parent node {parent_node_id} not found")

    repo_dir_path = Path(repo_dir).resolve()
    new_id = graph.next_id
    new_branch = f"mcgs/node-{new_id}"

    # Create worktree from parent
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"mcgs-hpo-reg-{new_id}-"))

    try:
        _run_git(["worktree", "add", str(worktree_dir), parent.branch], cwd=repo_dir_path)

        # Find and update HYPER_SPACE
        hs_file = find_hyper_space_file(worktree_dir, hyper_space_file or graph.config.hyper_space_file)
        if hs_file is None:
            raise ValueError("HYPER_SPACE not found in parent's code")

        source_code = hs_file.read_text(encoding="utf-8")
        modified_code = inject_params(source_code, best_params)
        hs_file.write_text(modified_code, encoding="utf-8")

        # Create new branch and commit
        _run_git(["checkout", "-b", new_branch], cwd=worktree_dir)
        git_commit_all(f"MCGS node {new_id}: HPO_tuned_{parent.short_name}", repo_dir=worktree_dir)

    finally:
        try:
            _run_git(["worktree", "remove", str(worktree_dir), "--force"], cwd=repo_dir_path, check=False)
        except Exception:
            pass
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)

    # Register node in graph
    from register_node import register_node
    node_id = register_node(
        graph_path=graph_path,
        short_name=f"HPO_tuned_{parent.short_name}"[:40],
        branch=new_branch,
        parent_edges_json=json.dumps([{"node_id": parent_node_id, "weight": 1.0}]),
        description=f"HPO-tuned from node {parent_node_id}. Params: {json.dumps(best_params)[:100]}",
        is_hpo_tuned=True,
    )
    return node_id


def maybe_run_tuning(
    graph_path: str,
    repo_dir: str = ".",
    max_iter: int = 50,
    backend_name: str = "optuna",
    timeout: int = 300,
) -> Optional[int]:
    """Run HPO on the best untuned design if conditions are met.

    Conditions:
    - tuned/total ratio < hpo_max_ratio
    - There exists an untuned, evaluated node with HYPER_SPACE

    Returns new node ID if tuning occurred, None otherwise.
    """
    graph = load_graph(graph_path)

    # Check ratio
    total = len(graph.nodes)
    tuned = sum(1 for n in graph.nodes if n.is_hpo_tuned)
    if total > 0 and tuned / total >= graph.config.hpo_max_ratio:
        print(f"[HPO] Skipping: {tuned}/{total} nodes are tuned (max ratio {graph.config.hpo_max_ratio})")
        return None

    # Find best untuned evaluated node
    evaluated = [
        n for n in graph.nodes
        if n.status == "evaluated" and n.objective is not None and not n.is_hpo_tuned
    ]
    if not evaluated:
        print("[HPO] No untuned evaluated nodes")
        return None

    if graph.config.minimize:
        best = min(evaluated, key=lambda n: n.objective)
    else:
        best = max(evaluated, key=lambda n: n.objective)

    print(f"[HPO] Tuning node {best.id} ({best.short_name}, objective={best.objective:.4f})")

    result = tune_node(
        graph_path=graph_path,
        node_id=best.id,
        repo_dir=repo_dir,
        max_iter=max_iter,
        backend_name=backend_name,
        timeout=timeout,
    )

    if "error" in result:
        print(f"[HPO] Failed: {result['error']}")
        return None

    if not result.get("best_params"):
        print("[HPO] No improvement found")
        return None

    # Register tuned node
    new_id = register_tuned_node(
        graph_path=graph_path,
        parent_node_id=best.id,
        best_params=result["best_params"],
        repo_dir=repo_dir,
    )

    improvement = ""
    if result.get("parent_metric") is not None and result.get("best_metric") is not None:
        delta = result["best_metric"] - result["parent_metric"]
        improvement = f" (delta={delta:+.4f})"

    print(f"[HPO] Created tuned node {new_id}{improvement}")
    return new_id


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Hyperparameter optimization for MCGS")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--repo-dir", default=".", help="Path to the git repository")
    parser.add_argument("--node-id", type=int, default=None, help="Node ID to tune")
    parser.add_argument("--max-iter", type=int, default=50, help="Max HPO iterations")
    parser.add_argument("--backend", default="optuna", help="HPO backend: optuna or hebo")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per experiment (seconds)")
    parser.add_argument("--hyper-space-file", default="",
                        help="Path to file containing HYPER_SPACE (relative to repo root)")
    parser.add_argument("--register", action="store_true",
                        help="Register the tuned node in the graph")
    parser.add_argument("--auto", action="store_true",
                        help="Automatically select the best untuned node")
    args = parser.parse_args()

    if args.auto:
        result = maybe_run_tuning(
            graph_path=args.graph,
            repo_dir=args.repo_dir,
            max_iter=args.max_iter,
            backend_name=args.backend,
            timeout=args.timeout,
        )
        if result is not None:
            print(json.dumps({"new_node_id": result}))
        sys.exit(0 if result is not None else 1)

    if args.node_id is None:
        print("Error: --node-id required (or use --auto)", file=sys.stderr)
        sys.exit(1)

    result = tune_node(
        graph_path=args.graph,
        node_id=args.node_id,
        repo_dir=args.repo_dir,
        max_iter=args.max_iter,
        backend_name=args.backend,
        timeout=args.timeout,
        hyper_space_file=args.hyper_space_file,
    )

    if "error" in result:
        print(json.dumps(result, indent=2))
        sys.exit(1)

    if args.register and result.get("best_params"):
        new_id = register_tuned_node(
            graph_path=args.graph,
            parent_node_id=args.node_id,
            best_params=result["best_params"],
            repo_dir=args.repo_dir,
            hyper_space_file=args.hyper_space_file,
        )
        result["new_node_id"] = new_id

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
