# MCGS Algorithm Reference

This document describes the Monte-Carlo Graph Search (MCGS) algorithm as adapted from the paper "Scientific discovery as meta-optimization." Read this to understand the mathematical foundations.

## Core Idea

MCGS balances **exploitation** (building on the best designs) with **exploration** (trying under-explored lineages) using Upper Confidence Bound (UCB) scores. Each design is a node in a directed acyclic graph (DAG), where edges represent parent-child relationships with influence weights.

## Graph Structure

- **Nodes**: Each design/modification of the codebase is a node. Stored as a git branch.
- **Edges**: Directed edges from parent to child, weighted by how much the parent influenced the child's design. Weights are self-reported by the designer agent and sum to 1.0.
- **DAG property**: A node can have multiple parents (if the designer drew from multiple references), making this a graph, not a tree.

## UCB Score Formula

```
UCB_j = r_j + c * sqrt(N_total) / (1 + n_j)
```

| Symbol | Meaning | Typical Value |
|--------|---------|---------------|
| `r_j` | Normalized rank score of node j (1.0 = best, 0.0 = worst) | [0, 1] |
| `c` | Exploration constant | 0.1 |
| `N_total` | Sum of all visit counts across all nodes | varies |
| `n_j` | Visit count of node j | >= 1.0 |

**Interpretation**:
- The **exploitation term** (`r_j`) favors nodes with good objectives
- The **exploration term** (`c * sqrt(N_total) / (1 + n_j)`) favors under-visited lineages
- As a lineage gets more visits, its exploration bonus shrinks, pushing the planner toward less-explored regions

## Visit Count Propagation

Visit counts track how thoroughly each lineage has been explored. When a new node j is created with parent edges {(p_k, w_k)}:

1. For each parent p_k: add weight w_k to p_k's visit count
2. Continue upward via BFS: for each ancestor, propagate credit attenuated by:
   ```
   credit * ancestor_edge_weight * decay^(depth+1)
   ```
3. `decay` (κ) = 0.9 — concentrates credit near immediate parents
4. Stop propagating when credit drops below 1e-4

**Why this matters**: A node that has been heavily referenced (and whose children have been heavily referenced) accumulates high visit counts. This makes it less likely to be selected again, pushing exploration toward fresher lineages.

## Rank Score Computation

Rank scores normalize objective values to [0, 1]:

1. Collect objectives from all evaluated nodes
2. Sort by objective value (ascending for minimization)
3. Assign rank scores linearly: worst = 0.0, best = 1.0
4. Nodes without objectives (failed/pending) get rank_score = 0.0

Using ranks instead of raw objectives makes UCB robust to objective scale — whether the objective ranges from 0-1 or 0-1,000,000, the UCB formula works the same.

## The MCGS Loop

Each iteration:
1. **Recompute** all visit counts from scratch (rebuild from genealogy)
2. **Recompute** rank scores from current objectives
3. **Compute** UCB scores for all nodes
4. **Planner** reads UCB-ranked list, selects reference nodes and research direction
5. **Designer** makes one modification, reports reference weights
6. **Execution engine** evaluates the new node's objective
7. **Record** results and loop

The graph is rebuilt every iteration to reflect the latest objective values and genealogy.

## Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `c_puct` | 0.1 | Exploration constant. Higher = more exploration. |
| `decay_factor` | 0.9 | Depth decay for visit propagation. Lower = more concentrated near parents. |
| `min_contribution` | 1e-4 | Propagation cutoff threshold. |

## Differences from Standard MCTS

| Standard MCTS | MCGS |
|---------------|------|
| Tree structure | DAG (multiple parents) |
| Uniform parent influence | Weighted parent edges |
| Strict expansion (one child at a time) | Any node can be expanded |
| Playouts/rollouts for evaluation | Actual code execution for objective |
| Win/loss statistics | Scalar objective values |

---

## Multi-Objective Mode: Consensus Objective Aggregation

When multiple objectives are active, the system uses a consensus mechanism to produce a single robust ranking from many potentially disagreeing objective functions.

### Why Consensus?

A single objective is vulnerable to Goodhart's Law: "when a measure becomes a target, it ceases to be a good measure." Multiple objectives capture different quality dimensions. Objectives that agree on design rankings are more likely to reflect genuine progress; those that disagree are suppressed.

### The Consensus Algorithm (6 Steps)

**Step 1 — Score Matrix**: Evaluate all K objective functions on all n designs:
```
S[i][j] = objective_i(experiment_results_j)   for all objectives i, designs j
```
Invalid/missing scores are set to +inf.

**Step 2 — Rank Conversion**: Convert scores to ranks per objective (lower score = rank 0 = best). Ties broken by node ID for stability.

**Step 3 — Kendall Tau Matrix**: Compute pairwise rank correlations between all objective pairs:
```
tau[i][k] = KendallTau(ranks_i, ranks_k)   for all objective pairs
```
Values range from -1 (perfect disagreement) to +1 (perfect agreement).

**Step 4 — Objective Weights**: Combine agreement, age decay, and meta-agent adjustments:
```
agreement_i = max(median(tau[i][k] for k != i), 0)     # suppress disagreeing objectives
age_decay_i = lambda ^ (current_iteration - created_iteration)   # phase out old objectives
meta_weight_i = weight multiplier from meta-agent (default 1.0)

w_i = agreement_i * age_decay_i * meta_weight_i
Normalize: w_i = w_i / sum(all w)
```

| Component | Purpose | Default |
|-----------|---------|---------|
| Agreement (median tau) | Self-correcting: outlier objectives get near-zero weight | — |
| Age decay (lambda) | Shifts influence from old to new objectives | 0.9 |
| Meta-weight | Manual override from meta-agent analysis | 1.0 |

**Step 5 — Meta-Agent Adjustment** (optional): If the meta-agent has set weight multipliers, they are applied and weights renormalized.

**Step 6 — Consensus Score** (weighted Borda count):
```
C_j = sum(w_i * R[i][j] / (n - 1))   for all objectives i
```
Result is in [0, 1] where 0 = best design. This consensus score is used as the `node.objective` value for UCB computation.

### Meta-Agent Oversight

The meta-agent (the orchestrating Claude session) runs periodically to:
1. **Analyze correlations**: identify clusters of redundant objectives and outliers
2. **Adjust weights**: amplify useful objectives, suppress misleading ones
3. **Guide evolution**: direct the Objective Agent toward unexplored quality dimensions
4. **Assess research phase**: exploring / converging / stuck / breakthrough_needed / refining

See `references/meta_agent_guide.md` for decision criteria and output format.

### Objective Agent

The Objective Agent is a Claude subagent that generates new Python objective functions. Each captures a different dimension of design quality. The consensus mechanism ensures that even if some generated objectives are poor, the overall ranking stays robust.

See `references/objective_agent_guide.md` for the subagent's instructions and output format.
