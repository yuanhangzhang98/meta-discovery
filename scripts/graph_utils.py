#!/usr/bin/env python3
"""Graph utilities for MCGS: data structures, JSON I/O, and git operations.

This module provides the core data layer for the Monte-Carlo Graph Search skill.
It handles:
  - Loading/saving the mcgs_graph.json metadata file
  - CRUD operations on graph nodes
  - Objective metadata and meta-agent state tracking
  - Git operations: init, branch creation, worktree management, diffing
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ParentEdge:
    """An edge from a parent node to a child, with influence weight."""
    node_id: int
    weight: float  # How much this parent influenced the child (sums to 1.0)


@dataclass
class ObjectiveMeta:
    """Metadata for a generated objective function."""
    id: int
    name: str
    filename: str           # e.g. "objective_0.py"
    description: str
    created_iteration: int  # which MCGS iteration spawned this
    weight: float = 1.0     # meta-agent weight multiplier (default neutral)
    weight_adder: float = 0.0  # meta-agent additive override (bypasses agreement-based zero)
    active: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ObjectiveMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MetaState:
    """Persistent state from the meta-agent's latest analysis."""
    research_phase: str = "exploring"  # exploring | converging | stuck | breakthrough_needed | refining
    research_assessment: str = ""
    objective_directions: str = ""
    weight_adjustments: Dict[str, float] = field(default_factory=dict)  # obj_name -> multiplier
    weight_adders: Dict[str, float] = field(default_factory=dict)  # obj_name -> additive override
    history: List[Dict[str, Any]] = field(default_factory=list)  # past analysis snapshots

    def to_dict(self) -> dict:
        return {
            "research_phase": self.research_phase,
            "research_assessment": self.research_assessment,
            "objective_directions": self.objective_directions,
            "weight_adjustments": self.weight_adjustments,
            "weight_adders": self.weight_adders,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MetaState":
        return cls(
            research_phase=d.get("research_phase", "exploring"),
            research_assessment=d.get("research_assessment", ""),
            objective_directions=d.get("objective_directions", ""),
            weight_adjustments=d.get("weight_adjustments", {}),
            weight_adders=d.get("weight_adders", {}),
            history=d.get("history", []),
        )

    def snapshot(self) -> Dict[str, Any]:
        """Create a snapshot of the current state for history."""
        return {
            "research_phase": self.research_phase,
            "research_assessment": self.research_assessment,
            "objective_directions": self.objective_directions,
            "weight_adjustments": dict(self.weight_adjustments),
            "weight_adders": dict(self.weight_adders),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@dataclass
class IterationState:
    """Tracks progress through the current MCGS iteration for run_step.py."""
    iteration: int = 0                                    # current iteration number
    step: str = "start"                                   # current step in the state machine
    periodic_tasks: List[str] = field(default_factory=list)  # tasks due this iteration
    planner_output: Optional[Dict[str, Any]] = None       # cached planner result
    designer_worktree: str = ""                            # path to active worktree
    parent_node_id: Optional[int] = None                  # primary parent for this iteration
    reference_node_ids: List[int] = field(default_factory=list)
    new_node_id: Optional[int] = None                     # node created this iteration
    completed_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IterationState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class GraphNode:
    """A single node (design) in the MCGS graph."""
    id: int
    branch: str
    short_name: str
    parent_edges: List[ParentEdge] = field(default_factory=list)
    objective: Optional[float] = None
    visit_count: float = 1.0
    rank_score: float = 0.0
    ucb_score: float = 0.0
    timestamp: str = ""
    description: str = ""
    status: str = "pending"  # pending | evaluated | failed
    stdout: str = ""
    stderr: str = ""
    # Multi-objective fields
    experiment_results: Optional[Dict[str, Any]] = None  # raw JSON from experiment script
    objective_scores: Optional[Dict[str, float]] = None   # obj_name -> score
    consensus_score: Optional[float] = None                # weighted Borda consensus
    # Multi-fidelity fields
    fidelity_level: int = 0                                # 0=low, 1=medium, 2=high
    fidelity_results: Dict[str, Any] = field(default_factory=dict)  # tier_name -> results
    # HPO fields
    is_hpo_tuned: bool = False                             # True if created by HPO

    def to_dict(self) -> dict:
        d = asdict(self)
        d["parent_edges"] = [asdict(e) for e in self.parent_edges]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GraphNode":
        edges = [ParentEdge(**e) for e in d.get("parent_edges", [])]
        return cls(
            id=d["id"],
            branch=d["branch"],
            short_name=d.get("short_name", ""),
            parent_edges=edges,
            objective=d.get("objective"),
            visit_count=d.get("visit_count", 1.0),
            rank_score=d.get("rank_score", 0.0),
            ucb_score=d.get("ucb_score", 0.0),
            timestamp=d.get("timestamp", ""),
            description=d.get("description", ""),
            status=d.get("status", "pending"),
            stdout=d.get("stdout", ""),
            stderr=d.get("stderr", ""),
            experiment_results=d.get("experiment_results"),
            objective_scores=d.get("objective_scores"),
            consensus_score=d.get("consensus_score"),
            fidelity_level=d.get("fidelity_level", 0),
            fidelity_results=d.get("fidelity_results", {}),
            is_hpo_tuned=d.get("is_hpo_tuned", False),
        )


@dataclass
class GraphConfig:
    """Configuration for the MCGS search."""
    c_puct: float = 0.1
    decay_factor: float = 0.9
    min_contribution: float = 1e-4
    objective_script: str = "evaluate.py"
    research_goal: str = ""
    minimize: bool = True
    # Multi-objective configuration
    experiment_script: str = ""          # script outputting JSON metrics (empty = single-objective mode)
    objectives_dir: str = "mcgs_objectives"  # directory storing objective .py files
    objective_interval: int = 5          # generate new objective every N iterations
    meta_interval: int = 10              # run meta-agent analysis every N iterations
    age_decay: float = 0.9              # lambda for objective age decay in consensus
    # Multi-fidelity configuration
    multi_fidelity: bool = False         # enable multi-fidelity execution
    fidelity_tiers: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"name": "low", "timeout": 60, "env": {"MCGS_FIDELITY": "low"}},
        {"name": "medium", "timeout": 300, "env": {"MCGS_FIDELITY": "medium"}},
        {"name": "high", "timeout": 1800, "env": {"MCGS_FIDELITY": "high"}},
    ])
    promotion_thresholds: List[float] = field(default_factory=lambda: [0.5, 0.1])
    # HPO configuration
    hpo_backend: str = "optuna"          # "optuna" or "hebo"
    hpo_interval: int = 10               # run HPO every N iterations
    hpo_max_iter: int = 50               # max HPO iterations per run
    hpo_max_ratio: float = 0.1           # max ratio of tuned to total nodes
    hyper_space_file: str = ""            # path to file containing HYPER_SPACE (relative to repo root)
    # Data isolation: paths (relative to repo root) symlinked into eval worktrees
    data_dirs: List[str] = field(default_factory=list)
    # Stop conditions (0 = disabled)
    max_iterations: int = 0              # stop after N iterations
    max_no_improve: int = 0              # stop after N iterations with no improvement over best
    max_time_minutes: int = 0            # stop after N minutes (wall clock from first iteration)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GraphConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def multi_objective(self) -> bool:
        """Whether multi-objective mode is active."""
        return bool(self.experiment_script)

    @property
    def eval_script(self) -> str:
        """The script to run for evaluation (experiment_script or objective_script)."""
        return self.experiment_script if self.multi_objective else self.objective_script


@dataclass
class MCGSGraph:
    """The full MCGS graph state."""
    config: GraphConfig = field(default_factory=GraphConfig)
    nodes: List[GraphNode] = field(default_factory=list)
    next_id: int = 0
    total_iterations: int = 0
    # Multi-objective state
    objectives: List[ObjectiveMeta] = field(default_factory=list)
    meta_state: Optional[MetaState] = None
    # Accumulated lessons for subagent context
    lessons_learned: List[str] = field(default_factory=list)
    # Iteration state machine (for run_step.py)
    iteration_state: Optional[IterationState] = None

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = {
            "config": self.config.to_dict(),
            "nodes": [n.to_dict() for n in self.nodes],
            "next_id": self.next_id,
            "total_iterations": self.total_iterations,
        }
        if self.objectives:
            d["objectives"] = [o.to_dict() for o in self.objectives]
        if self.meta_state is not None:
            d["meta_state"] = self.meta_state.to_dict()
        if self.lessons_learned:
            d["lessons_learned"] = self.lessons_learned
        if self.iteration_state is not None:
            d["iteration_state"] = self.iteration_state.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MCGSGraph":
        config = GraphConfig.from_dict(d.get("config", {}))
        nodes = [GraphNode.from_dict(n) for n in d.get("nodes", [])]
        objectives = [ObjectiveMeta.from_dict(o) for o in d.get("objectives", [])]
        meta_state = MetaState.from_dict(d["meta_state"]) if "meta_state" in d else None
        iteration_state = IterationState.from_dict(d["iteration_state"]) if "iteration_state" in d else None
        return cls(
            config=config,
            nodes=nodes,
            next_id=d.get("next_id", 0),
            total_iterations=d.get("total_iterations", 0),
            objectives=objectives,
            meta_state=meta_state,
            lessons_learned=d.get("lessons_learned", []),
            iteration_state=iteration_state,
        )

    # ── Node operations ──────────────────────────────────────────────────

    def get_node(self, node_id: int) -> Optional[GraphNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def add_node(
        self,
        short_name: str,
        branch: str,
        parent_edges: Optional[List[ParentEdge]] = None,
        description: str = "",
    ) -> GraphNode:
        """Create a new node and add it to the graph. Returns the new node."""
        node = GraphNode(
            id=self.next_id,
            branch=branch,
            short_name=short_name,
            parent_edges=parent_edges or [],
            timestamp=datetime.now(timezone.utc).isoformat(),
            description=description,
            status="pending",
        )
        self.nodes.append(node)
        self.next_id += 1
        return node

    def get_children(self, node_id: int) -> List[GraphNode]:
        """Get all nodes that have node_id as a parent."""
        children = []
        for n in self.nodes:
            for edge in n.parent_edges:
                if edge.node_id == node_id:
                    children.append(n)
                    break
        return children

    # ── Objective operations ─────────────────────────────────────────────

    def get_objective(self, obj_id: int) -> Optional[ObjectiveMeta]:
        """Get objective metadata by ID."""
        for o in self.objectives:
            if o.id == obj_id:
                return o
        return None

    def get_active_objectives(self) -> List[ObjectiveMeta]:
        """Get all active objectives."""
        return [o for o in self.objectives if o.active]

    def add_objective(
        self,
        name: str,
        filename: str,
        description: str,
        created_iteration: int,
    ) -> ObjectiveMeta:
        """Add a new objective to the graph. Returns the new objective."""
        obj_id = max((o.id for o in self.objectives), default=-1) + 1
        obj = ObjectiveMeta(
            id=obj_id,
            name=name,
            filename=filename,
            description=description,
            created_iteration=created_iteration,
        )
        self.objectives.append(obj)
        return obj

    def apply_meta_weights(self, weight_adjustments: Dict[str, float]) -> None:
        """Apply meta-agent weight multipliers to objectives by name."""
        for obj in self.objectives:
            if obj.name in weight_adjustments:
                obj.weight = weight_adjustments[obj.name]

    def apply_meta_adders(self, adder_adjustments: Dict[str, float]) -> None:
        """Apply meta-agent additive weight overrides to objectives by name.

        The adder bypasses the agreement-based zero in consensus, giving the
        objective guaranteed influence even when negatively correlated with others.
        """
        for obj in self.objectives:
            if obj.name in adder_adjustments:
                obj.weight_adder = adder_adjustments[obj.name]

    def add_lesson(self, text: str) -> None:
        """Add a lesson learned if not already present (deduplicates)."""
        if text not in self.lessons_learned:
            self.lessons_learned.append(text)

    def get_best_node(self) -> Optional[GraphNode]:
        """Get the node with the best objective value. Returns None if no evaluated nodes."""
        evaluated = [n for n in self.nodes if n.status == "evaluated" and n.objective is not None]
        if not evaluated:
            return None
        if self.config.minimize:
            return min(evaluated, key=lambda n: n.objective)
        return max(evaluated, key=lambda n: n.objective)

    @staticmethod
    def node_branch_name(node_id: int) -> str:
        """Standard branch name for a node."""
        return f"mcgs/node-{node_id}"

    def check_stop_conditions(self) -> Optional[str]:
        """Check whether the search should stop.

        Returns a reason string if a stop condition is met, None otherwise.
        """
        cfg = self.config
        iters = self.total_iterations

        # Max iterations
        if cfg.max_iterations > 0 and iters >= cfg.max_iterations:
            return f"Reached max_iterations ({cfg.max_iterations})"

        # Max wall-clock time (compare first node timestamp to now)
        if cfg.max_time_minutes > 0 and self.nodes:
            first_ts = self.nodes[0].timestamp
            if first_ts:
                try:
                    start = datetime.fromisoformat(first_ts)
                    elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60
                    if elapsed >= cfg.max_time_minutes:
                        return f"Reached max_time_minutes ({cfg.max_time_minutes}, elapsed={elapsed:.0f})"
                except (ValueError, TypeError):
                    pass

        # No improvement over last N iterations
        if cfg.max_no_improve > 0 and iters >= cfg.max_no_improve:
            best = self.get_best_node()
            if best is not None:
                # Count consecutive non-improving iterations from the tail
                evaluated = sorted(
                    [n for n in self.nodes if n.status == "evaluated" and n.objective is not None],
                    key=lambda n: n.id,
                )
                if len(evaluated) >= cfg.max_no_improve + 1:
                    recent = evaluated[-(cfg.max_no_improve):]
                    all_worse = all(
                        (n.objective >= best.objective if cfg.minimize else n.objective <= best.objective)
                        and n.id != best.id
                        for n in recent
                    )
                    if all_worse:
                        return (
                            f"No improvement in last {cfg.max_no_improve} iterations "
                            f"(best: node {best.id}, objective={best.objective:.4f})"
                        )

        return None


# ──────────────────────────────────────────────────────────────────────────────
# JSON I/O
# ──────────────────────────────────────────────────────────────────────────────

def load_graph(path: str | Path) -> MCGSGraph:
    """Load graph from a JSON file."""
    path = Path(path)
    if not path.exists():
        return MCGSGraph()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return MCGSGraph.from_dict(data)


def save_graph(graph: MCGSGraph, path: str | Path) -> None:
    """Save graph to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, indent=2, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# Git operations
# ──────────────────────────────────────────────────────────────────────────────

def run_git(args: List[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


# Backward-compatible alias
_run_git = run_git


def git_init(repo_dir: str | Path) -> None:
    """Initialize a git repo if not already initialized. Creates an initial commit."""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").exists():
        _run_git(["init"], cwd=repo_dir)

    # Ensure there's at least one commit (branches need a commit to work)
    result = _run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False)
    if result.returncode != 0:
        # No commits yet — stage everything and create initial commit
        _run_git(["add", "-A"], cwd=repo_dir)
        _run_git(["commit", "-m", "Initial commit", "--allow-empty"], cwd=repo_dir)


def git_create_branch(branch_name: str, from_ref: str = "HEAD", repo_dir: str | Path = ".") -> None:
    """Create a new branch from a given ref."""
    _run_git(["branch", branch_name, from_ref], cwd=repo_dir)


def git_checkout(branch_name: str, repo_dir: str | Path = ".") -> None:
    """Check out a branch."""
    _run_git(["checkout", branch_name], cwd=repo_dir)


def git_current_branch(repo_dir: str | Path = ".") -> str:
    """Get the current branch name."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    return result.stdout.strip()


def git_commit_all(message: str, repo_dir: str | Path = ".") -> str:
    """Stage all changes and commit. Returns the commit hash."""
    _run_git(["add", "-A"], cwd=repo_dir)
    # Check if there are changes to commit
    result = _run_git(["status", "--porcelain"], cwd=repo_dir)
    if not result.stdout.strip():
        # No changes — return current HEAD
        head = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
        return head.stdout.strip()
    _run_git(["commit", "-m", message], cwd=repo_dir)
    head = _run_git(["rev-parse", "HEAD"], cwd=repo_dir)
    return head.stdout.strip()


def git_diff(ref_a: str, ref_b: str, repo_dir: str | Path = ".") -> str:
    """Get the diff between two refs."""
    result = _run_git(["diff", ref_a, ref_b], cwd=repo_dir, check=False)
    return result.stdout


def git_diff_stat(ref_a: str, ref_b: str, repo_dir: str | Path = ".") -> str:
    """Get a summary stat of diff between two refs."""
    result = _run_git(["diff", "--stat", ref_a, ref_b], cwd=repo_dir, check=False)
    return result.stdout


def git_create_worktree(worktree_path: str | Path, branch: str, repo_dir: str | Path = ".") -> Path:
    """Create a git worktree for a branch. Returns the worktree path."""
    worktree_path = Path(worktree_path)
    _run_git(["worktree", "add", str(worktree_path), branch], cwd=repo_dir)
    return worktree_path


def git_remove_worktree(worktree_path: str | Path, repo_dir: str | Path = ".") -> None:
    """Remove a git worktree."""
    _run_git(["worktree", "remove", str(worktree_path), "--force"], cwd=repo_dir, check=False)


def git_branch_exists(branch_name: str, repo_dir: str | Path = ".") -> bool:
    """Check if a branch exists."""
    result = _run_git(["rev-parse", "--verify", branch_name], cwd=repo_dir, check=False)
    return result.returncode == 0


def git_list_branches(repo_dir: str | Path = ".", pattern: str = "mcgs/node-*") -> List[str]:
    """List branches matching a pattern."""
    result = _run_git(["branch", "--list", pattern], cwd=repo_dir, check=False)
    return [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n") if b.strip()]


@contextmanager
def managed_worktree(
    branch: str,
    repo_dir: str | Path = ".",
    prefix: str = "mcgs-wt-",
    data_dirs: Optional[List[str]] = None,
) -> Generator[Path, None, None]:
    """Context manager that creates a temporary worktree and cleans it up on exit.

    Usage:
        with managed_worktree("mcgs/node-5", repo_dir=".") as wt:
            # wt is a Path to the worktree directory
            do_work(wt)
        # worktree is removed automatically
    """
    repo_dir = Path(repo_dir).resolve()
    worktree_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        run_git(["worktree", "add", str(worktree_dir), branch], cwd=repo_dir)
        if data_dirs:
            symlink_data_dirs(data_dirs, repo_dir, worktree_dir)
        yield worktree_dir
    finally:
        try:
            run_git(["worktree", "remove", str(worktree_dir), "--force"],
                    cwd=repo_dir, check=False)
        except Exception:
            pass
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)


def symlink_data_dirs(data_dirs: List[str], repo_dir: Path, worktree_dir: Path) -> None:
    """Symlink configured data directories into a worktree."""
    for data_dir in data_dirs:
        source = repo_dir / data_dir
        target = worktree_dir / data_dir
        if not source.exists():
            print(f"  Warning: data_dir '{data_dir}' not found at {source}", file=sys.stderr)
            continue
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(source), str(target), target_is_directory=source.is_dir())
        except OSError:
            shutil.copytree(str(source), str(target))


def cleanup_stale_worktrees(repo_dir: str | Path = ".") -> List[str]:
    """Remove stale MCGS worktrees (mcgs-worktree-* and mcgs-eval-*).

    Returns list of removed worktree paths.
    """
    repo_dir = Path(repo_dir)
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_dir, check=False)
    if result.returncode != 0:
        return []

    removed = []
    current_path = None
    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line == "" and current_path:
            # Check if it's an MCGS worktree
            name = Path(current_path).name
            if name.startswith("mcgs-worktree-") or name.startswith("mcgs-eval-"):
                _run_git(["worktree", "remove", current_path, "--force"],
                         cwd=repo_dir, check=False)
                removed.append(current_path)
            current_path = None

    # Also prune any dangling worktree references
    _run_git(["worktree", "prune"], cwd=repo_dir, check=False)
    return removed


# ──────────────────────────────────────────────────────────────────────────────
# Graph formatting (for planner prompts)
# ──────────────────────────────────────────────────────────────────────────────

def format_node_table(graph: MCGSGraph) -> str:
    """Format the graph nodes as a markdown table, sorted by UCB score descending."""
    nodes = sorted(graph.nodes, key=lambda n: n.ucb_score, reverse=True)

    lines = []
    lines.append("| ID | Name | Objective | Visits | Rank | UCB | Status | Parents |")
    lines.append("|----|------|-----------|--------|------|-----|--------|---------|")

    for n in nodes:
        obj_str = f"{n.objective:.4f}" if n.objective is not None else "N/A"
        parents_str = ", ".join(
            f"{e.node_id}({e.weight:.2f})" for e in n.parent_edges
        ) if n.parent_edges else "-"
        lines.append(
            f"| {n.id} | {n.short_name} | {obj_str} | {n.visit_count:.2f} "
            f"| {n.rank_score:.3f} | {n.ucb_score:.3f} | {n.status} | {parents_str} |"
        )
    return "\n".join(lines)


def format_graph_summary(graph: MCGSGraph) -> str:
    """Format summary statistics for the planner."""
    evaluated = [n for n in graph.nodes if n.objective is not None]
    if not evaluated:
        return "No nodes have been evaluated yet."

    objectives = [n.objective for n in evaluated]
    best = min(objectives) if graph.config.minimize else max(objectives)
    best_node = next(n for n in evaluated if n.objective == best)
    mean_obj = sum(objectives) / len(objectives)
    frontier = [n for n in graph.nodes if n.status == "pending"]

    lines = [
        f"Total nodes: {len(graph.nodes)}",
        f"Evaluated: {len(evaluated)}",
        f"Failed: {len([n for n in graph.nodes if n.status == 'failed'])}",
        f"Pending: {len(frontier)}",
        f"Best objective: {best:.4f} (node {best_node.id}: {best_node.short_name})",
        f"Mean objective: {mean_obj:.4f}",
        f"Total iterations: {graph.total_iterations}",
        f"Optimization direction: {'minimize' if graph.config.minimize else 'maximize'}",
    ]

    # Add multi-objective info if active
    if graph.config.multi_objective and graph.objectives:
        active = graph.get_active_objectives()
        lines.append(f"Active objectives: {len(active)}")
        if graph.meta_state:
            lines.append(f"Research phase: {graph.meta_state.research_phase}")

    return "\n".join(lines)


def format_objective_table(graph: MCGSGraph) -> str:
    """Format objectives as a markdown table for prompts."""
    if not graph.objectives:
        return "No objectives defined."

    lines = []
    lines.append("| ID | Name | Weight | Created | Active | Description |")
    lines.append("|----|------|--------|---------|--------|-------------|")

    for o in graph.objectives:
        lines.append(
            f"| {o.id} | {o.name} | {o.weight:.2f} | iter {o.created_iteration} "
            f"| {'yes' if o.active else 'no'} | {o.description[:60]} |"
        )
    return "\n".join(lines)


def format_consensus_summary(stats: Dict[str, Any]) -> str:
    """Format consensus aggregation stats for reporting."""
    lines = [
        f"Consensus Objective Summary:",
        f"  Active objectives: {stats.get('num_objectives', 0)}",
        f"  Evaluated designs: {stats.get('num_designs', 0)}",
    ]

    weights = stats.get("weights", {})
    if weights:
        lines.append("  Objective weights:")
        for name, w in sorted(weights.items(), key=lambda x: -x[1]):
            lines.append(f"    {name}: {w:.3f}")

    meta_applied = stats.get("meta_weights_applied", False)
    if meta_applied:
        lines.append("  Meta-agent weight adjustments: applied")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point (for quick inspection)
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MCGS graph utilities")
    parser.add_argument("--graph", default="mcgs_graph.json", help="Path to graph JSON")
    parser.add_argument("--action",
                        choices=["show", "summary", "table", "objectives",
                                 "cleanup-worktrees", "add-lesson"],
                        default="show")
    parser.add_argument("--repo-dir", default=".", help="Path to the git repository")
    parser.add_argument("--text", default="", help="Text for add-lesson action")
    args = parser.parse_args()

    if args.action == "cleanup-worktrees":
        removed = cleanup_stale_worktrees(args.repo_dir)
        if removed:
            print(f"Removed {len(removed)} stale worktrees:")
            for p in removed:
                print(f"  {p}")
        else:
            print("No stale worktrees found.")
        sys.exit(0)

    graph = load_graph(args.graph)
    if args.action == "show":
        print(json.dumps(graph.to_dict(), indent=2))
    elif args.action == "summary":
        print(format_graph_summary(graph))
    elif args.action == "table":
        print(format_node_table(graph))
    elif args.action == "objectives":
        print(format_objective_table(graph))
    elif args.action == "add-lesson":
        if not args.text:
            print("Error: --text required for add-lesson", file=sys.stderr)
            sys.exit(1)
        graph.add_lesson(args.text)
        save_graph(graph, args.graph)
        print(f"Added lesson. Total: {len(graph.lessons_learned)}")
