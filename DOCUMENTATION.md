# Meta-Discovery: Technical Documentation

This document provides a comprehensive reference for the Meta-Discovery skill — an automated research system built as a Claude Code skill. It covers the system architecture, workflow, data model, all scripts, agent guides, and configuration options.

For the mathematical foundations, see [`references/mcgs_algorithm.md`](references/mcgs_algorithm.md).
For the orchestration instructions, see [`SKILL.md`](SKILL.md) (slim router) and [`phases/`](phases/) (phase-specific instructions).
For the lightweight project index, see [`CLAUDE.md`](CLAUDE.md).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Data Model](#3-data-model)
4. [Workflow: The MCGS Loop](#4-workflow-the-mcgs-loop)
5. [Scripts Reference](#5-scripts-reference)
6. [Agent Guides Reference](#6-agent-guides-reference)
7. [Configuration Options](#7-configuration-options)
8. [Multi-Fidelity Execution](#8-multi-fidelity-execution)
9. [Hyperparameter Optimization](#9-hyperparameter-optimization)
10. [Validation and Feedback](#10-validation-and-feedback)
11. [Conventions and Patterns](#11-conventions-and-patterns)
12. [File Inventory](#12-file-inventory)

---

## 1. System Overview

Meta-Discovery turns Claude into an orchestrator that iteratively modifies a codebase, evaluates changes, and uses UCB-based exploration-exploitation to guide the search. It implements the framework from [Zhang, Sipling & Di Ventra (2026)](https://www.researchsquare.com/article/rs-9108409/v1).

**Two modes:**

| Mode | Evaluation | Scoring | Best for |
|------|-----------|---------|----------|
| **Single-objective** | Script outputs a float | Direct comparison | Well-defined metrics |
| **Multi-objective** | Script outputs JSON metrics | Consensus aggregation via Kendall tau | Complex/exploratory research |

**Core loop** (each iteration):
1. Planner (LLM) analyzes search history → selects direction
2. Designer (LLM) makes one focused code change
3. Deterministic pipeline: validate → commit → execute → score → UCB update

All designs live on git branches (`mcgs/node-*`). The search history is a DAG stored in `mcgs_graph.json`.

---

## 2. Architecture

### 2.1 Four LLM Agents

| Agent | Role | Mode | Frequency |
|-------|------|------|-----------|
| **Planner** | Analyzes UCB-ranked designs, proposes research directions | Read-only | Every iteration |
| **Designer** | Makes ONE focused code modification | Code-modifying | Every iteration |
| **Objective Agent** | Generates proxy objective functions | Read-only (orchestrator writes) | Every `objective_interval` iterations |
| **Meta-Agent** | Analyzes correlations, adjusts weights, sets strategy | Orchestrator itself | Every `meta_interval` iterations |

### 2.2 Deterministic Helper Scripts

All evaluation, scoring, and graph management is handled by Python scripts — no LLM involved. This ensures reproducibility and reduces orchestrator boilerplate.

```
scripts/
├── graph_utils.py          # Data layer: structures, JSON I/O, git operations
├── init_mcgs.py            # Initialize MCGS in a repository
├── execute_node.py         # Run experiment on a node's code
├── run_objectives.py       # Score a node with all active objectives
├── consensus.py            # Multi-objective consensus aggregation
├── compute_ucb.py          # UCB score computation
├── register_node.py        # Register a new node in the graph
├── run_iteration.py        # Full post-designer pipeline (Steps 7-11)
├── validate_agent_output.py # Validate subagent outputs
├── multi_fidelity.py       # Multi-fidelity execution engine
└── hpo_tune.py             # Hyperparameter optimization
```

### 2.3 Data Flow

```
                    ┌─────────────────┐
                    │  Orchestrator   │
                    │  (Claude SKILL) │
                    └──────┬──────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         ┌────▼────┐  ┌───▼────┐  ┌───▼───────────┐
         │ Planner │  │Designer│  │Objective Agent │
         │(read-only)│ │(code)  │  │(read-only)     │
         └────┬────┘  └───┬────┘  └───┬───────────┘
              │            │            │
              ▼            ▼            ▼
         ┌─────────────────────────────────┐
         │      run_iteration.py           │
         │  validate → commit → execute    │
         │  → score → consensus → UCB      │
         └──────────────┬──────────────────┘
                        │
                        ▼
                  mcgs_graph.json
```

---

## 3. Data Model

All state is stored in `mcgs_graph.json`. The schema is defined in `scripts/graph_utils.py`.

### 3.1 MCGSGraph (top-level)

| Field | Type | Description |
|-------|------|-------------|
| `config` | GraphConfig | Search configuration |
| `nodes` | List[GraphNode] | All designs explored |
| `next_id` | int | Next node ID to assign |
| `total_iterations` | int | Total loop iterations completed |
| `objectives` | List[ObjectiveMeta] | Multi-objective function metadata |
| `meta_state` | MetaState | Latest meta-agent analysis |
| `lessons_learned` | List[str] | Accumulated context for subagent prompts |

### 3.2 GraphNode

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Unique identifier |
| `branch` | str | Git branch name (e.g., `mcgs/node-5`) |
| `short_name` | str | Descriptive name (≤40 chars) |
| `parent_edges` | List[ParentEdge] | Parents with influence weights (sum to 1.0) |
| `objective` | float \| None | Scalar objective (lower = better if minimizing) |
| `visit_count` | float | MCGS visit count (for exploration bonus) |
| `rank_score` | float | Normalized rank [0, 1] (1.0 = best) |
| `ucb_score` | float | UCB score (rank + exploration bonus) |
| `status` | str | `"pending"` \| `"evaluated"` \| `"failed"` |
| `experiment_results` | dict \| None | Raw JSON from experiment script |
| `objective_scores` | dict \| None | Per-objective scores: `{obj_name: float}` |
| `consensus_score` | float \| None | Weighted Borda consensus |
| `fidelity_level` | int | Multi-fidelity tier: 0=low, 1=medium, 2=high |
| `fidelity_results` | dict | Results by tier: `{tier_name: results}` |
| `is_hpo_tuned` | bool | True if created by HPO |
| `stdout` / `stderr` | str | Captured experiment output (last 2000 chars) |

### 3.3 GraphConfig

| Field | Default | Description |
|-------|---------|-------------|
| `c_puct` | 0.1 | UCB exploration constant |
| `decay_factor` | 0.9 | Visit count propagation decay |
| `min_contribution` | 1e-4 | Stop propagating below this threshold |
| `objective_script` | `"evaluate.py"` | Single-objective evaluation script |
| `experiment_script` | `""` | Multi-objective experiment script (empty = single-obj mode) |
| `research_goal` | `""` | Human-provided research objective |
| `minimize` | True | Optimization direction |
| `objectives_dir` | `"mcgs_objectives"` | Directory for objective .py files |
| `objective_interval` | 5 | Generate new objective every N iterations |
| `meta_interval` | 10 | Run meta-agent analysis every N iterations |
| `age_decay` | 0.9 | Lambda for objective age decay in consensus |
| `multi_fidelity` | False | Enable multi-fidelity execution |
| `fidelity_tiers` | 3 tiers | Tier definitions (name, timeout, env vars) |
| `promotion_thresholds` | [0.5, 0.1] | Promotion percentiles (top 50%, top 10%) |
| `hpo_backend` | `"optuna"` | HPO backend: `"optuna"` or `"hebo"` |
| `hpo_interval` | 10 | Run HPO every N iterations |
| `hpo_max_iter` | 50 | Max HPO iterations per run |
| `hpo_max_ratio` | 0.1 | Max ratio of tuned to total nodes |
| `hyper_space_file` | `""` | Path to file containing HYPER_SPACE |

### 3.4 ObjectiveMeta

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Unique identifier |
| `name` | str | Snake_case name |
| `filename` | str | File in objectives_dir (e.g., `objective_0.py`) |
| `description` | str | What this objective measures |
| `created_iteration` | int | When it was generated |
| `weight` | float | Meta-agent multiplier (default 1.0) |
| `weight_adder` | float | Meta-agent additive override (default 0.0) |
| `active` | bool | Whether included in consensus |

### 3.5 MetaState

| Field | Type | Description |
|-------|------|-------------|
| `research_phase` | str | `exploring` \| `converging` \| `stuck` \| `breakthrough_needed` \| `refining` |
| `research_assessment` | str | Natural language analysis |
| `objective_directions` | str | Guidance for next Objective Agent |
| `weight_adjustments` | dict | Objective name → multiplier |
| `weight_adders` | dict | Objective name → additive override |
| `history` | list | Past analysis snapshots |

---

## 4. Workflow: The MCGS Loop

The orchestration instructions are split into phase files loaded on-demand to save context:
- `phases/setup.md` — Phase 1 (read once during setup)
- `phases/loop.md` — Phase 2 (read during iteration loop)
- `phases/summary.md` — Phase 3 (read at the end)
- `phases/notes.md` — reference notes (read once during setup)

### Phase 1: Setup (one-time)

1. **Understand the project**: read code, identify metrics, find key source files
2. **Choose mode**: single-objective or multi-objective
3. **Write experiment script**: outputs float (single) or JSON (multi)
   - Optional: extract tunable constants to `experiment_config.yaml`
   - Optional: support `MCGS_FIDELITY` env var for multi-fidelity
   - Optional: declare `HYPER_SPACE` for HPO
4. **Initialize MCGS**: `python init_mcgs.py --repo-dir . --research-goal "..." ...`
5. **Evaluate baseline** (node-0)
6. **Brief the user**

### Phase 2: Iteration Loop

Each iteration (orchestrator drives):

```
Step 1:  Load state, compute UCB
Step 2:  [Periodic] Objective Agent → new objective function
Step 3:  [Periodic] Meta-Agent analysis → weight adjustments
Step 4:  Planner → research_direction, reference_node_ids
Step 5:  Create designer worktree
Step 6:  Designer → code changes + mcgs_design_output.json
Step 7-11: run_iteration.py (single script):
           validate → commit → execute → score → consensus → UCB
Step 11.5: [Periodic] HPO on best untuned design
Step 12: Report progress to user
Step 13: Continue or stop
```

### Phase 3: Summary

- Best design, improvement over baseline, full diff
- Objective evolution, consensus stability (multi-obj)
- Offer to apply best design to working code
- **Research report generation**: `generate_report.py` creates publication-quality figures and a LaTeX scaffold; the agent fills in narrative sections to produce a standalone academic paper

---

## 5. Scripts Reference

### 5.1 `graph_utils.py` — Data Layer

The core data module. Provides all data structures, JSON I/O, git operations, and graph formatting.

**Key functions:**

| Function | Description |
|----------|-------------|
| `load_graph(path)` | Load MCGSGraph from JSON |
| `save_graph(graph, path)` | Save MCGSGraph to JSON |
| `cleanup_stale_worktrees(repo_dir)` | Remove lingering MCGS worktrees |
| `format_node_table(graph)` | Markdown table of nodes ranked by UCB |
| `format_graph_summary(graph)` | Summary statistics for prompts |
| `format_objective_table(graph)` | Markdown table of objectives |
| `format_consensus_summary(stats)` | Format consensus stats |

**Key methods on MCGSGraph:**

| Method | Description |
|--------|-------------|
| `add_node(short_name, branch, parent_edges, description)` | Create and register a new node |
| `get_node(node_id)` | Look up node by ID |
| `get_children(node_id)` | All nodes with this node as parent |
| `add_objective(name, filename, description, created_iteration)` | Register a new objective |
| `get_active_objectives()` | All active objectives |
| `apply_meta_weights(adjustments)` | Set meta-agent multipliers |
| `apply_meta_adders(adjustments)` | Set meta-agent additive overrides |
| `add_lesson(text)` | Add lesson learned (deduplicates) |

**CLI:**
```bash
python graph_utils.py --graph mcgs_graph.json --action show|summary|table|objectives|cleanup-worktrees|add-lesson
```

---

### 5.2 `init_mcgs.py` — Initialization

Sets up git, creates baseline node-0, initializes `mcgs_graph.json`.

```bash
python init_mcgs.py \
    --repo-dir . \
    --objective-script evaluate.py \
    --research-goal "Improve algorithm scaling" \
    --minimize \
    --experiment-script run_experiment.py  # enables multi-objective mode
```

---

### 5.3 `execute_node.py` — Experiment Execution

Creates a temporary worktree for a node's branch, runs the experiment/objective script, parses output, updates graph, cleans up.

```bash
python execute_node.py \
    --node-id 5 \
    --graph mcgs_graph.json \
    --repo-dir . \
    --timeout 300 \
    --fidelity low   # optional: sets env vars from tier config
```

**Output parsing:**
1. Tries JSON (last stdout line) → stores as `experiment_results`
2. Falls back to float → stores as `objective`
3. Neither → marks node as `"failed"`

---

### 5.4 `run_objectives.py` — Per-Node Objective Scoring

Evaluates all active objective functions on a single node's `experiment_results`. Lightweight — use after `execute_node.py` to populate `objective_scores`.

```bash
python run_objectives.py --node-id 5 --graph mcgs_graph.json
```

---

### 5.5 `consensus.py` — Multi-Objective Consensus

Implements the 5-step consensus pipeline:

1. **Score matrix**: evaluate all objectives on all nodes
2. **Rank conversion**: lower score = rank 0 = best
3. **Kendall tau matrix**: pairwise rank correlations
4. **Objective weights**: `max(agreement × age_decay × multiplier + adder, 0)`, normalized
5. **Consensus score**: weighted Borda count ∈ [0, 1] (lower = better)

```bash
python consensus.py --graph mcgs_graph.json --verbose
```

**Weight formula detail:**
- `agreement` = max(median Kendall tau with other objectives, 0) — suppresses outliers
- `age_decay` = `age_decay_param ^ (current_iteration - created_iteration)` — phases out old objectives
- `multiplier` = `objective.weight` (meta-agent, default 1.0)
- `adder` = `objective.weight_adder` (meta-agent, default 0.0) — bypasses agreement-based zero. **Use sparingly (0.01–0.05)**; agreement-based weights are typically 0.0–0.3 before normalization, so even small adders can dominate. An adder of 0.1 on a zero-agreement objective can give it >50% of total weight.

---

### 5.6 `compute_ucb.py` — UCB Scores

Three-step pipeline:

1. **Propagate visit counts**: BFS from each node upward through parents, attenuated by edge weights and decay
2. **Compute rank scores**: normalize objectives to [0, 1]
3. **Compute UCB**: `UCB_j = rank_j + c_puct × sqrt(N_total) / (1 + n_j)`

```bash
python compute_ucb.py --graph mcgs_graph.json
```

---

### 5.7 `register_node.py` — Node Registration

Eliminates manual boilerplate for adding nodes to the graph.

```bash
python register_node.py \
    --graph mcgs_graph.json \
    --short-name "sigmoid_gate" \
    --description "Added sigmoid gating to loss" \
    --branch mcgs/node-5 \
    --parent-edges '[{"node_id": 3, "weight": 0.7}, {"node_id": 7, "weight": 0.3}]' \
    --hpo-tuned  # optional flag
```

Outputs JSON with the new `node_id`. Increments `total_iterations` by default.

---

### 5.8 `run_iteration.py` — Post-Designer Pipeline

Replaces the manual Steps 7-11 sequence with a single script.

**Validate only** (check before committing):
```bash
python run_iteration.py validate \
    --worktree /tmp/mcgs-worktree-5 \
    --reference-nodes "3,7" \
    --protected "run_experiment.py,mcgs_graph.json" \
    --parent-branch mcgs/node-3 \
    --graph mcgs_graph.json
```

**Full pipeline** (validate + commit + execute + score + UCB):
```bash
python run_iteration.py run \
    --worktree /tmp/mcgs-worktree-5 \
    --reference-nodes "3,7" \
    --protected "run_experiment.py,mcgs_graph.json" \
    --parent-branch mcgs/node-3 \
    --graph mcgs_graph.json \
    --repo-dir . \
    --parent-edges '[{"node_id": 3, "weight": 0.7}, {"node_id": 7, "weight": 0.3}]' \
    --timeout 300
```

**Output**: JSON with `validation`, `commit` (node_id, branch), and `execution` (status, objective, ucb_score).

**Timeouts**: The `--timeout` flag is propagated to `execute_node.py` with an additional 60s overhead for worktree setup/teardown. Scoring and consensus scripts get 120s; UCB gets 60s. If any subprocess times out, a clean error is returned instead of crashing.

**Error handling**: If validation fails, exits with `action_needed: "fix_and_retry"`. The orchestrator can SendMessage to the Designer, then re-run.

---

### 5.9 `validate_agent_output.py` — Subagent Validation

Detect-only validation (no auto-fix) for all three subagent types.

| Subcommand | Checks |
|------------|--------|
| `validate-planner` | JSON parseable; `research_direction`, `reference_node_ids`, `current_phase`, `key_insights`, `focus_areas`, `avoid_areas` present and correctly typed |
| `validate-designer` | `short_name` (≤40 chars), `description`, `reference_weights` (array-of-objects, weights sum to 1.0, all reference nodes covered) |
| `validate-objective` | Python compiles; `objective()` function exists with 1 param; no forbidden imports; test-call returns finite float; metadata has `name` and `description` |
| `check-protected` | `git diff --name-only` + untracked files matched against `fnmatch` patterns |

All return JSON: `{"valid": bool, "errors": [...]}` or `{"violations": [...]}`.

---

### 5.10 `multi_fidelity.py` — Multi-Fidelity Engine

Implements the paper's multi-fidelity evaluation strategy.

| Subcommand | Description |
|------------|-------------|
| `execute --node-id N` | Execute a node at its current fidelity level |
| `promote-sweep` | Check all nodes for promotion, optionally re-execute promoted ones |
| `check --node-id N` | Check if a specific node qualifies for promotion |

**Promotion rules:**
- Top 50% by consensus at current tier → promote to next tier
- Configurable via `config.promotion_thresholds`

**Fidelity tiers** (configurable in `GraphConfig.fidelity_tiers`):

| Tier | Default Timeout | Env Var |
|------|----------------|---------|
| low | 60s | `MCGS_FIDELITY=low` |
| medium | 300s | `MCGS_FIDELITY=medium` |
| high | 1800s | `MCGS_FIDELITY=high` |

---

### 5.11 `hpo_tune.py` — Hyperparameter Optimization

Tunes hyperparameters declared via `HYPER_SPACE` in source code. Supports pluggable backends.

**HYPER_SPACE convention** (in the design's source file):
```python
HYPER_SPACE = {
    "learning_rate": dict(type="log_uniform", default=0.001, low=1e-5, high=0.1),
    "momentum": dict(type="uniform", default=0.9, low=0.0, high=0.99),
    "hidden_dim": dict(type="int", default=128, low=32, high=512),
    "activation": dict(type="categorical", default="relu", choices=["relu", "gelu", "silu"]),
}
```

**Supported types:** `uniform`, `log_uniform`, `int`, `categorical`, `bool`

**Backends:**

| Backend | Requirements | Notes |
|---------|-------------|-------|
| **Optuna** (default) | `pip install optuna` | TPE sampler, works with any Python/numpy |
| **HEBO** | Python ≤3.10, numpy <1.25, torch | Best benchmarks, poorly maintained |

**Usage:**
```bash
# Auto-select best untuned node:
python hpo_tune.py --graph mcgs_graph.json --auto --max-iter 50

# Tune specific node:
python hpo_tune.py --graph mcgs_graph.json --node-id 5 --register --max-iter 50

# Use HEBO backend:
python hpo_tune.py --graph mcgs_graph.json --auto --backend hebo
```

**Process:**
1. Creates a worktree for the node's branch
2. Finds and extracts `HYPER_SPACE` (AST parsing)
3. Optimization loop: suggest → inject params → run experiment → observe metric
4. Warm-starts from parent's default params + metric
5. If `--register`: creates a new node `HPO_tuned_{parent_name}` with `is_hpo_tuned=True`

---

### 5.12 Report Generation Scripts

Five scripts for generating a LaTeX research report with publication-quality figures.

**`plot_style.py`** — Shared matplotlib configuration (headless-safe `Agg` backend, 300 DPI, Arial/Helvetica). All plot scripts import `setup_style()` and `save_figure()`.

**`plot_convergence.py`** — Two figures:
- `convergence.pdf`: Best-so-far objective vs node creation order
- `node_comparison.pdf`: Horizontal bar chart of top-N nodes by objective

```bash
python plot_convergence.py --graph mcgs_graph.json --output-dir figures/ --top-n 20
```

**`plot_dag.py`** — Design genealogy DAG visualization. Default: graphviz `dot` layout (requires pygraphviz or pydot). Fallback: pure-Python sugiyama layout. All nodes shown (no pruning). Nodes sized by objective quality, colored by objective/status/ID. Best node: red diamond. Baseline: gray circle.

```bash
python plot_dag.py --graph mcgs_graph.json --output-dir figures/ --color-by objective
```

**`plot_objectives.py`** — Combined multi-panel figure (multi-objective mode only):
- Panel (a): Kendall tau heatmap
- Panel (b): Objective weights bar chart
- Panel (c): PCA embedding (requires scikit-learn, ≥3 objectives)

```bash
python plot_objectives.py --graph mcgs_graph.json --objectives-dir mcgs_objectives/ --output-dir figures/
```

**`generate_report.py`** — Preparation step: generates all standard figures and writes `data_summary.json` with extracted statistics (best node, improvement, top designs, lessons learned, config). The agent then writes the LaTeX report from scratch with full control over structure and narrative. Helper functions (`escape_latex`, `build_lineage`, `compile_pdf`) are importable.

```bash
python generate_report.py --graph mcgs_graph.json --output-dir mcgs_report/ [--objectives-dir mcgs_objectives/]
```

Output: `mcgs_report/figures/*.{png,pdf}` + `mcgs_report/data_summary.json`. The agent writes the `.tex` file and compiles it.

---

## 6. Agent Guides Reference

Located in `references/`. These are injected into subagent prompts by the orchestrator.

| Guide | Purpose | Key content |
|-------|---------|-------------|
| [`planner_guide.md`](references/planner_guide.md) | Strategic direction | How to read UCB table, choose reference nodes, write research directions |
| [`designer_guide.md`](references/designer_guide.md) | Code modification | ONE modification rule, modifiable vs protected files, `mcgs_design_output.json` schema, HYPER_SPACE, experiment config |
| [`objective_agent_guide.md`](references/objective_agent_guide.md) | Objective generation | Holistic proxy philosophy, `def objective(experiment_results) -> float`, constraints (pure Python, deterministic, no I/O) |
| [`meta_agent_guide.md`](references/meta_agent_guide.md) | Research oversight | Phase classification, weight multipliers + adders, objective directions |
| [`mcgs_algorithm.md`](references/mcgs_algorithm.md) | Math reference | UCB formula, visit count propagation, rank normalization, consensus algorithm |

---

## 7. Configuration Options

Configuration lives in `mcgs_graph.json` under the `config` key. Set during `init_mcgs.py` or modified programmatically.

### Core MCGS
| Parameter | Description | Tuning guidance |
|-----------|-------------|-----------------|
| `c_puct` | Exploration constant | Higher → more exploration. Default 0.1 works well for most cases. |
| `decay_factor` | Visit count propagation decay | 0.9 concentrates credit near immediate parents |
| `minimize` | Optimization direction | True for loss/error, False for accuracy/score |

### Multi-Objective
| Parameter | Description |
|-----------|-------------|
| `experiment_script` | Non-empty → enables multi-objective mode |
| `objective_interval` | How often to generate new objectives |
| `meta_interval` | How often to run meta-agent analysis |
| `age_decay` | Exponential decay for older objectives in consensus |

### Multi-Fidelity
| Parameter | Description |
|-----------|-------------|
| `multi_fidelity` | Set True to enable |
| `fidelity_tiers` | List of `{name, timeout, env}` tier definitions |
| `promotion_thresholds` | Percentile thresholds for each promotion step |

### HPO
| Parameter | Description |
|-----------|-------------|
| `hpo_backend` | `"optuna"` or `"hebo"` |
| `hpo_interval` | How often to check if HPO should run |
| `hpo_max_iter` | Budget per HPO run |
| `hpo_max_ratio` | Max fraction of tuned nodes |
| `hyper_space_file` | Hint for which file contains HYPER_SPACE |

---

## 8. Multi-Fidelity Execution

Based on the paper's multi-fidelity schedule. Designs progress through tiers:

```
All designs → Low fidelity (fast screening)
                    │
              Top 50% → Medium fidelity (moderate evaluation)
                              │
                        Top 10% → High fidelity (thorough benchmark)
```

**How it works:**
1. Every new design starts at `fidelity_level=0` (low)
2. `execute_node.py --fidelity low` sets `MCGS_FIDELITY=low` env var; the experiment script adjusts its budget accordingly
3. After consensus is computed, `multi_fidelity.py promote-sweep` checks each node's percentile rank among peers at the same fidelity level
4. Promoted nodes are re-executed at the higher fidelity level
5. `node.experiment_results` always reflects the highest-fidelity run (backward compatible with consensus)

**User's experiment script** should read the env var:
```python
import os
fidelity = os.environ.get("MCGS_FIDELITY", "low")
if fidelity == "low":
    epochs, dataset_size = 5, 100
elif fidelity == "medium":
    epochs, dataset_size = 20, 1000
else:  # high
    epochs, dataset_size = 100, 10000
```

---

## 9. Hyperparameter Optimization

Designs can declare tunable hyperparameters via a `HYPER_SPACE` dict. The HPO module optimizes these without changing architecture.

**Flow:**
1. Orchestrator calls `hpo_tune.py --auto` (or targets a specific node)
2. Script finds `HYPER_SPACE` in the node's code (AST parsing)
3. Runs optimization loop: suggest → inject defaults → run experiment → observe
4. Creates a new node with tuned params: `HPO_tuned_{parent_name}`

**When HPO runs:**
- Periodically (every `hpo_interval` iterations) when tuned/total ratio < `hpo_max_ratio`
- On-demand when the orchestrator or user requests it

**Separation of concerns:**
- Designer: architecture/algorithm changes (structural)
- HPO: hyperparameter tuning (numeric optimization)

---

## 10. Validation and Feedback

Every subagent output is validated before use. The pattern is:

```
Agent produces output
        │
        ▼
validate_agent_output.py (detect errors)
        │
    ┌───┴───┐
    │ valid │ invalid
    ▼       ▼
  proceed  SendMessage to agent (1 retry)
                │
            ┌───┴───┐
            │ fixed │ still broken
            ▼       ▼
          proceed  fallback (safe defaults or skip)
```

**Planner fallback**: top-UCB node, generic direction
**Designer fallback**: equal weights, generated short_name, revert protected files
**Objective Agent fallback**: skip this objective, continue loop

### Protected File Enforcement

The orchestrator lists protected files (experiment scripts, test data, `mcgs_graph.json`) in the Designer's prompt. Enforcement is two-layered:

1. **Prompt instruction**: Designer is told not to modify protected files
2. **Post-hoc verification**: `check-protected` uses `git diff --name-only` + `fnmatch` to detect violations
3. **Feedback**: violations trigger a SendMessage asking the Designer to revert
4. **Fallback**: orchestrator reverts via `git checkout {parent_branch} -- {file}`

### Lessons Learned

The `lessons_learned` list in `mcgs_graph.json` accumulates context across iterations:
- Protected file constraints
- Common format errors
- Patterns that consistently fail
- Any insight the orchestrator deems useful for subagents

This list is injected into every Planner and Designer prompt, preventing repeated mistakes.

---

## 11. Conventions and Patterns

### 11.1 Experiment Config (`experiment_config.yaml`)

Extract tunable experiment parameters into a YAML file that the experiment script reads. This lets Designers change parameters without modifying protected scripts.

```yaml
# experiment_config.yaml (modifiable)
loss_type: "cie"
k_train: 5
learning_rate: 0.001
```

```python
# run_experiment.py (protected)
import yaml
with open("experiment_config.yaml") as f:
    config = yaml.safe_load(f)
```

### 11.2 HYPER_SPACE Declaration

Designers declare tunable hyperparameters for automatic optimization:

```python
HYPER_SPACE = {
    "param_name": dict(
        type="uniform|log_uniform|int|categorical|bool",
        default=<value>,
        low=<min>,       # for numeric types
        high=<max>,      # for numeric types
        choices=[...],   # for categorical
    ),
}
```

### 11.3 Designer Output (`mcgs_design_output.json`)

**Required fields** (all mandatory):
```json
{
  "short_name": "descriptive_name_under_40_chars",
  "description": "What was changed and why.",
  "reference_weights": [
    {"node_id": 3, "weight": 0.7},
    {"node_id": 7, "weight": 0.3}
  ]
}
```

- `reference_weights` MUST be array-of-objects (not dict)
- Weights MUST sum to 1.0
- All reference node IDs from the planner must be included

### 11.4 Git Branching

- All MCGS work on `mcgs/node-*` branches
- User's main/master branch is never modified
- Worktrees are created in `/tmp/mcgs-worktree-{id}` (designer) and `/tmp/mcgs-eval-{id}` (execution)
- Worktrees are cleaned up after use; `cleanup_stale_worktrees()` handles stragglers

---

## 12. File Inventory

```
meta-discovery/
├── CLAUDE.md                        # Lightweight project index (keep updated)
├── DOCUMENTATION.md                 # This file (keep updated)
├── SKILL.md                         # Skill entry point (slim router → phases/)
├── README.md                        # Project overview, installation, citation
├── requirements.txt                 # Python dependencies (all optional)
├── LICENSE                          # MIT License
│
├── phases/                          # Orchestration instructions (loaded on-demand)
│   ├── setup.md                     # Phase 1: project understanding, experiment script, init
│   ├── loop.md                      # Phase 2: the MCGS iteration loop (Steps 1–13)
│   ├── summary.md                   # Phase 3: report results, offer to apply best design
│   └── notes.md                     # Reference notes on subagents, git, errors, convergence
│
├── references/                      # Agent prompt guides (injected into subagent prompts)
│   ├── mcgs_algorithm.md            # Mathematical foundations
│   ├── planner_guide.md             # Planner agent instructions
│   ├── designer_guide.md            # Designer agent instructions
│   ├── objective_agent_guide.md     # Objective agent instructions
│   └── meta_agent_guide.md          # Meta-agent analysis framework
│
└── scripts/                         # Deterministic Python helpers (no LLM)
    ├── graph_utils.py               # Data model, JSON I/O, git operations
    ├── init_mcgs.py                 # Initialize MCGS in a repository
    ├── execute_node.py              # Run experiment on a node (fidelity-aware)
    ├── run_objectives.py            # Score node with all objectives
    ├── consensus.py                 # Multi-objective consensus aggregation
    ├── compute_ucb.py               # UCB score computation
    ├── register_node.py             # Register new node in graph (CLI)
    ├── run_iteration.py             # Post-designer pipeline (validate → commit → execute → score → UCB)
    ├── validate_agent_output.py     # Subagent output validation + protected file check
    ├── multi_fidelity.py            # Multi-fidelity execution engine (tier promotion)
    ├── hpo_tune.py                  # Hyperparameter optimization (Optuna/HEBO)
    ├── plot_style.py                # Shared matplotlib style (headless Agg backend, 300 DPI)
    ├── plot_convergence.py          # Convergence curve + node comparison bar chart
    ├── plot_dag.py                  # DAG visualization (graphviz default, sugiyama fallback)
    ├── plot_objectives.py           # Combined objective analysis figure (multi-obj only)
    └── generate_report.py           # LaTeX report scaffold + figure orchestrator
```

**Runtime artifacts** (created in user's project directory):
- `mcgs_graph.json` — search state (the single source of truth)
- `mcgs_objectives/` — generated objective .py files
- `mcgs/node-*` — git branches for each design
- `mcgs_report/` — generated LaTeX report, figures, and compiled PDF

---

## Maintenance

**After modifying any file in this project, update both `CLAUDE.md` and this file (`DOCUMENTATION.md`) to reflect the changes.** See `CLAUDE.md` for the lightweight index.
