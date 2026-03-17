---
name: meta-discovery
description: "Meta-Discovery: LLM-driven automated scientific research and code optimization using Monte-Carlo Graph Search (MCGS) with consensus objective aggregation. Supports single-objective mode (simple scalar evaluation) and multi-objective mode where evaluation criteria co-evolve alongside solutions, guided by meta-agent oversight. Use this skill whenever the user wants to systematically explore a design space by iteratively modifying a codebase, evaluating changes against objectives, and using UCB-based exploration-exploitation to guide the search. Triggers on: 'meta-discovery', 'Monte Carlo Graph Search', 'MCGS', 'automated research loop', 'automated discovery', 'explore design space', 'UCB search', 'exploration-exploitation optimization', 'planner and designer agents', 'iterative code optimization with LLM', 'multi-objective optimization', 'consensus objective', 'meta-optimization', 'scientific discovery', or any request to run an automated loop that repeatedly modifies code, evaluates it, and decides what to try next."
---

# Meta-Discovery Skill

This skill turns you (Claude) into the orchestrator of an automated research system. You drive a loop that uses specialized subagents — a **Planner** (read-only, strategic), a **Designer** (code-modifying, tactical), and optionally an **Objective Agent** (generates evaluation functions) — along with deterministic helper scripts for evaluation, consensus aggregation, and UCB score computation.

Each design is a git branch. The search history is a directed acyclic graph stored in `mcgs_graph.json`. The UCB algorithm balances exploiting the best designs with exploring under-visited lineages.

## Two Modes

- **Single-objective mode** (default): one evaluation script outputs a float. Simple and fast. Good when you know exactly what metric to optimize.
- **Multi-objective mode**: an experiment script outputs JSON metrics; multiple objective functions score those metrics; consensus aggregation combines them into a single robust ranking. Objectives co-evolve over time, guided by meta-agent analysis. Use this when the research goal is complex or when a single metric could be gamed.

## When to Read Reference Files

Before starting, read `references/mcgs_algorithm.md` for the mathematical details (UCB formula, visit count propagation, rank normalization, consensus aggregation). You don't need to memorize it — the scripts implement the math — but understanding the concepts helps you explain progress to the user and make good judgment calls.

Read `references/planner_guide.md` and `references/designer_guide.md` before spawning those subagents — you'll inject their content into the subagent prompts.

For multi-objective mode, also read:
- `references/objective_agent_guide.md` — before spawning the Objective Agent
- `references/meta_agent_guide.md` — before performing meta-agent analysis

---

## Phase 1: Setup

The user will typically provide a high-level research goal and point you at their codebase. Your job is to understand their project, set up the evaluation infrastructure, and initialize the search. Don't just ask for everything upfront — be proactive: read their code, figure out what metrics matter, and propose a concrete plan.

### 1.1 Understand the Project

Start by exploring the user's codebase:

1. **Read the README, main scripts, and config files** to understand what the project does
2. **Identify the training/evaluation pipeline**: how is the code run? What metrics does it produce?
3. **Find the key source files** that the Designer agent will modify (models, loss functions, training loops)
4. **Check dependencies**: what needs to be installed? Are there GPU requirements?

From this exploration, determine:
- What **metrics** are available (loss, accuracy, energy, success rate, runtime, etc.)
- What is the natural **experiment** — a training run? a simulation? a benchmark?
- How long does one experiment take? (This determines the search budget)
- What aspects of the code are most promising to explore (architecture, loss, hyperparameters, algorithms)

### 1.2 Decide on Mode and Confirm with User

Based on your understanding, recommend single-objective or multi-objective mode:

- **Single-objective**: when there's one clear metric to optimize and the user knows what it is. Simpler, faster, good for well-defined optimization tasks.
- **Multi-objective** (recommended for research): when the goal is complex, multiple metrics matter, or a single metric could be gamed. The consensus mechanism prevents reward hacking and the Objective Agent discovers evaluation criteria the user might not have thought of.

Confirm with the user:
- Your understanding of the research goal
- The mode (single vs multi-objective)
- How many iterations to run (default: 10–20)
- Timeout per evaluation (default: 300s; adjust based on how long experiments take)

### 1.3 Write the Experiment Script

This is the most important setup step. You need to create a script that runs the project's experiment pipeline and outputs results.

**For single-objective mode**, create a script (e.g., `evaluate.py`) that:
- Runs the experiment with fixed, reproducible parameters
- Prints a single float as its **last stdout line** (the metric to optimize)

**For multi-objective mode**, create a script (e.g., `run_experiment.py`) that:
- Runs the experiment with fixed, reproducible parameters
- Collects all available metrics
- Prints a JSON object as its **last stdout line**

**Design principles for the experiment script:**

1. **Use a reduced budget** for fast iteration. If the full training takes 30 minutes, create a version that runs in 1-3 minutes (fewer epochs, smaller dataset, etc.). The search needs many iterations; each one must be fast.

2. **Fix the data and random seeds** so results are comparable across designs. The only thing that should change between nodes is the code — not the dataset or initialization randomness.

3. **Output all metrics you can extract**, even ones that seem secondary. The Objective Agent may find them useful later. Common metrics to include:
   - Primary performance metric (loss, accuracy, energy, etc.)
   - Convergence speed (steps to reach a threshold)
   - Computational cost (wall-clock time, memory)
   - Robustness indicators (variance across instances, worst-case performance)
   - Any domain-specific quality measures

4. **Handle errors gracefully**. If the modified code crashes, the script should catch the exception and either exit with non-zero status or output degraded metrics. Don't let one bad design break the entire loop.

5. **Extract tunable constants into `experiment_config.yaml`** (recommended). Move parameters like loss type, dataset size, K values, etc. into a YAML config file that the experiment script loads at startup. Add `experiment_config.yaml` to the **Modifiable files** list and keep the experiment script itself protected. This lets the Designer change experiment parameters without modifying the protected script.

6. **Support multi-fidelity** (optional). If experiments are expensive, have the script read `os.environ.get("MCGS_FIDELITY", "low")` and adjust its budget accordingly (e.g., fewer epochs/smaller problems at "low", full budget at "high"). This enables the multi-fidelity execution engine.

7. **Declare HYPER_SPACE** (optional). If the project has tunable hyperparameters, add a `HYPER_SPACE` dict in the main source file. This enables automatic hyperparameter optimization. Example:
   ```python
   HYPER_SPACE = {
       "learning_rate": dict(type="log_uniform", default=0.001, low=1e-5, high=0.1),
       "momentum": dict(type="uniform", default=0.9, low=0.0, high=0.99),
   }
   ```

**Example experiment script pattern (multi-objective):**
```python
#!/usr/bin/env python3
"""Meta-discovery experiment script. Runs a quick training loop and outputs JSON metrics."""
import json, sys, time

# Import the project's own training/evaluation functions
# (adjust these imports to match the actual project structure)
from project.train import train_model
from project.evaluate import evaluate_model

def main():
    start = time.time()
    try:
        # Run with reduced budget for fast iteration
        model = train_model(num_epochs=50, seed=42, dataset_seed=0)
        metrics = evaluate_model(model, test_seed=1)

        results = {
            "primary_metric": metrics["test_loss"],
            "accuracy": metrics["accuracy"],
            "convergence_epoch": metrics.get("convergence_epoch", 50),
            "wall_time_seconds": time.time() - start,
        }
    except Exception as e:
        # On failure, output degraded results rather than crashing
        results = {
            "primary_metric": float("inf"),
            "accuracy": 0.0,
            "convergence_epoch": float("inf"),
            "wall_time_seconds": time.time() - start,
            "error": str(e),
        }

    print(json.dumps(results))

if __name__ == "__main__":
    main()
```

After writing the script, **test it** by running it once to verify it completes and outputs valid JSON (or a float for single-objective). Fix any issues before proceeding.

### 1.4 Write Initial Objective Functions (Multi-Objective Only)

Create 2–3 objective functions that capture different quality dimensions. Each is a Python file in `{REPO_DIR}/mcgs_objectives/` defining:

```python
def objective(experiment_results: dict) -> float:
    """<what this measures>. Lower = better."""
    return some_score
```

Good initial objectives typically cover:
1. **Primary performance** — the main thing the user cares about (negated if higher-is-better)
2. **Efficiency** — convergence speed, computational cost, or sample efficiency
3. **Robustness** — worst-case performance, variance, or generalization gap

The Objective Agent will generate additional objectives during the search, so you don't need to be exhaustive here. Focus on the most important dimensions.

After writing them, register each in the graph's objectives list (the init script handles this if you pass the code, or you can edit `mcgs_graph.json` directly).

### 1.5 Initialize the Git Repository and MCGS State

Ensure the project is a git repo with a clean commit:

```bash
cd {REPO_DIR}
git init  # if not already
git add -A && git commit -m "Initial state for meta-discovery"
```

Then run initialization:

**Single-objective mode:**
```bash
python {SKILL_DIR}/scripts/init_mcgs.py \
    --repo-dir {REPO_DIR} \
    --objective-script {OBJECTIVE_SCRIPT} \
    --research-goal "{RESEARCH_GOAL}" \
    --minimize
```

**Multi-objective mode:**
```bash
python {SKILL_DIR}/scripts/init_mcgs.py \
    --repo-dir {REPO_DIR} \
    --experiment-script {EXPERIMENT_SCRIPT} \
    --research-goal "{RESEARCH_GOAL}" \
    --minimize \
    --objective-interval 5 \
    --meta-interval 10
```

This creates:
- Branch `mcgs/node-0` (the baseline snapshot)
- `mcgs_graph.json` with config and node-0
- (Multi-objective) `mcgs_objectives/` directory

If you wrote objectives in step 1.4, make sure they are committed and registered in the graph. You can add objectives to the graph programmatically:

```python
# After init, load graph, add objectives, save
from graph_utils import load_graph, save_graph, ObjectiveMeta
graph = load_graph("mcgs_graph.json")
graph.objectives.append(ObjectiveMeta(
    id=0, name="primary_metric", filename="objective_0.py",
    description="Negated primary performance metric", created_iteration=0
))
save_graph(graph, "mcgs_graph.json")
```

### 1.6 Evaluate the Baseline

Run the execution engine on node-0 to establish the baseline:

```bash
python {SKILL_DIR}/scripts/execute_node.py \
    --node-id 0 \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR}
```

For multi-objective mode, also score with all objectives and compute consensus:
```bash
python {SKILL_DIR}/scripts/run_objectives.py \
    --node-id 0 \
    --graph {REPO_DIR}/mcgs_graph.json

python {SKILL_DIR}/scripts/consensus.py \
    --graph {REPO_DIR}/mcgs_graph.json
```

Then update UCB scores:
```bash
python {SKILL_DIR}/scripts/compute_ucb.py --graph {REPO_DIR}/mcgs_graph.json
```

**Report the baseline to the user**: show them the metrics, confirm the experiment script works, and check that the results look reasonable. If the baseline is broken, fix it now — everything downstream depends on it.

### 1.7 Briefing Before Search

Before starting the loop, give the user a brief summary:
- Baseline metrics (what the unmodified code achieves)
- What the Designer will be allowed to modify (which files, what kinds of changes)
- The search budget (N iterations, estimated time)
- (Multi-objective) The initial objectives and what they measure

Confirm they're ready to start.

---

## Phase 2: The MCGS Loop

Repeat for each iteration (up to the user's requested count, or until they say stop):

### Step 1: Load Current State

Read `mcgs_graph.json` and run `compute_ucb.py` to ensure scores are current.

### Step 2: Objective Agent (Multi-Objective, Periodic)

**Skip this step** in single-objective mode or if `iteration % objective_interval != 0`.

When it's time to generate a new objective, spawn a **read-only subagent** for the Objective Agent:

**Subagent prompt template:**
```
You are the Objective Agent in a Monte-Carlo Graph Search system.

{contents of references/objective_agent_guide.md}

## Research Goal
{research_goal from config}

## Existing Objectives
{for each objective: name, description, code, current weight from format_objective_table()}

## Kendall Tau Correlation Matrix
{tau matrix from latest consensus.py run — read from graph stats or recompute}

## Example Experiment Results
{experiment_results from 2-3 recent evaluated nodes, formatted as JSON}

## Meta-Agent Directions
{graph.meta_state.objective_directions, if available, else "No specific guidance yet."}

Generate a new objective function. Output:
1. The Python code in a fenced ```python block
2. A JSON metadata block with "name" and "description"
```

After the subagent returns:
1. Extract the Python code and JSON metadata from the subagent's response
2. Save the objective file to `{objectives_dir}/objective_{next_id}.py`
3. Validate using the validation script:
   ```bash
   python {SKILL_DIR}/scripts/validate_agent_output.py validate-objective \
       --file {objectives_dir}/objective_{next_id}.py \
       --sample-results '{JSON experiment_results from a recent evaluated node}' \
       --metadata '{JSON metadata extracted from the subagent response}'
   ```
4. **If validation fails** → SendMessage to the Objective Agent (1 retry max):
   - Include the specific errors: `"Your objective function has these errors: {errors}. Fix the code and output corrected Python and JSON metadata."`
   - After the agent responds, extract the updated code, overwrite the file, and re-validate
5. **If still invalid after retry** → delete the file, log the failure, and continue (don't block the loop)
6. If valid: add to `graph.objectives` via `graph.add_objective(...)` and save graph

### Step 3: Meta-Agent Analysis (Multi-Objective, Periodic)

**Skip this step** in single-objective mode or if `iteration % meta_interval != 0`.

You (the orchestrator) perform this analysis directly — no subagent needed. Read `references/meta_agent_guide.md` for the decision framework.

1. Run `consensus.py --verbose` to get the latest correlation matrix and weights
2. Review the tau matrix: which objectives agree? Which are outliers?
3. Check progress: are recent designs improving?
4. Decide:
   - **Research phase**: exploring / converging / stuck / breakthrough_needed / refining
   - **Weight multipliers**: for each objective, set a multiplier (0.0–2.0)
   - **Weight adders**: for objectives suppressed by consensus but strategically important, set an adder (0.0–1.0) to guarantee influence despite low agreement
   - **Objective directions**: strategic guidance for the next Objective Agent invocation
5. Update `graph.meta_state` with your analysis:
   - Save a snapshot to `graph.meta_state.history`
   - Update `research_phase`, `research_assessment`, `objective_directions`, `weight_adjustments`, `weight_adders`
   - Apply weights: `graph.apply_meta_weights(weight_adjustments)`
   - Apply adders: `graph.apply_meta_adders(weight_adders)`
6. Save graph
7. Recompute consensus with updated weights:
   ```bash
   python {SKILL_DIR}/scripts/consensus.py --graph {REPO_DIR}/mcgs_graph.json
   python {SKILL_DIR}/scripts/compute_ucb.py --graph {REPO_DIR}/mcgs_graph.json
   ```

### Step 4: Run the Planner

Spawn a **read-only subagent** for the Planner role. Use the Agent tool with this structure:

**Subagent prompt template:**
```
You are the Planner Agent in a Monte-Carlo Graph Search system.

{contents of references/planner_guide.md}

## Research Goal
{research_goal from config}

## Project Context
{brief description: what the project does, key source files the Designer can modify,
 what kinds of changes are most promising — architecture, loss functions, algorithms, etc.}

## Graph Summary
{output of format_graph_summary()}

## Node Table (ranked by UCB)
{output of format_node_table()}

## Lessons Learned
{one line per entry from graph.lessons_learned, or "None yet." if empty}

## Available Commands
You can run these bash commands to inspect nodes:
- `git diff mcgs/node-X..mcgs/node-Y` — see code diff between two nodes
- `git show mcgs/node-X:path/to/file` — read a file on a specific node's branch

Analyze the search history and output your decision as a JSON block.
```

Parse the planner's JSON output to extract:
- `reference_node_ids` — which nodes to build from
- `research_direction` — what to try next
- `focus_areas` / `avoid_areas`

**Validate the planner output.** Save the planner's JSON to a temp file (or parse inline), then run:
```bash
python {SKILL_DIR}/scripts/validate_agent_output.py validate-planner \
    --file /tmp/planner_output.json
```

If validation fails → **SendMessage** to the Planner agent (1 retry max):
- Include the specific errors: `"Your output has these errors: {errors}. Output corrected JSON with all required fields: current_phase, key_insights, research_direction, reference_node_ids, focus_areas, avoid_areas."`
- After the agent responds, re-validate the corrected output

If still invalid after retry → **fallback**:
- Missing `reference_node_ids`: use the top-UCB evaluated node
- Missing `research_direction`: use `"Continue exploring from top-UCB node"`
- Missing other fields: use empty lists / `"early_exploration"`

### Step 5: Prepare the Designer's Workspace

The primary parent is `reference_node_ids[0]` — the node the designer will branch from.

Create a git worktree:
```bash
git worktree add /tmp/mcgs-worktree-{new_id} mcgs/node-{parent_id}
```

### Step 6: Run the Designer

Spawn a **code-modifying subagent** for the Designer role:

**Subagent prompt template:**
```
You are the Designer Agent in a Monte-Carlo Graph Search system.

{contents of references/designer_guide.md}

## Research Goal
{research_goal}

## Project Context
{brief description of the project — what it does, key source files, how experiments are run}

## Modifiable Files
{list of files/directories the designer is allowed to change — e.g., "src/models.py, src/losses.py, src/training.py"}

## Protected Files (DO NOT MODIFY)
{list of files that must not be changed — e.g., "run_experiment.py, mcgs_graph.json, test data"}

## Lessons Learned
{one line per entry from graph.lessons_learned, or "None yet." if empty}

## Planner's Direction
{planner's research_direction}

## Reference Nodes: {reference_node_ids}
Focus areas: {focus_areas}
Avoid: {avoid_areas}

## Your Task
Your working directory is /tmp/mcgs-worktree-{new_id}, checked out to node {parent_id}'s code.

Make ONE small, principled modification following the planner's direction.

You can inspect reference nodes with:
- `git diff mcgs/node-{parent_id}..mcgs/node-{ref_id}` to see what changed in a reference
- `git show mcgs/node-{ref_id}:path/to/file` to read files from a reference node

After making your changes, create mcgs_design_output.json with your short_name, description, and reference_weights.

Do NOT run the objective script — the execution engine handles evaluation.
```

### Steps 7–11: Validate, Commit, Execute, Score, UCB (Single Script)

After the designer finishes, use `run_iteration.py` which handles all deterministic steps in one call.

**First, validate** (check for errors before committing):
```bash
python {SKILL_DIR}/scripts/run_iteration.py validate \
    --worktree /tmp/mcgs-worktree-{new_id} \
    --reference-nodes {comma_separated_reference_node_ids} \
    --protected "{comma_separated_protected_files}" \
    --parent-branch mcgs/node-{parent_id} \
    --graph {REPO_DIR}/mcgs_graph.json
```

This outputs JSON with `validation.valid`, `validation.design_errors`, and `validation.protected_violations`.

**If validation fails** → SendMessage to the Designer (1 retry max):
- For format errors: `"Fix mcgs_design_output.json: {errors}"`
- For protected file violations: `"Revert these protected files: {list}. Use: git checkout mcgs/node-{parent_id} -- {file}"`
- After the Designer responds, re-run `validate`
- If still failing: apply fallback (equal weights, revert protected files manually), then proceed

**If validation passes** (or after fallback), run the full pipeline:
```bash
python {SKILL_DIR}/scripts/run_iteration.py run \
    --worktree /tmp/mcgs-worktree-{new_id} \
    --reference-nodes {comma_separated_reference_node_ids} \
    --protected "{comma_separated_protected_files}" \
    --parent-branch mcgs/node-{parent_id} \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR} \
    --parent-edges '{reference_weights_as_json_array}' \
    --timeout {TIMEOUT}
```

This single command:
1. Validates the design output and protected files
2. Creates branch `mcgs/node-{new_id}`, commits, registers node in graph
3. Removes the designer worktree (before execute, preventing "already checked out" errors)
4. Executes the experiment
5. Scores objectives (multi-objective mode)
6. Computes consensus (multi-objective mode)
7. Updates UCB scores

Output is a JSON summary with node status, objective value, and any errors.

**If multi-fidelity is enabled**, after the pipeline completes, periodically run a promotion sweep to advance top designs to higher fidelity:
```bash
python {SKILL_DIR}/scripts/multi_fidelity.py promote-sweep \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR}
```

**Lessons learned**: After each iteration, if something notable happened (validation error, failure with instructive error, protected file violation), add a lesson:
```bash
python {SKILL_DIR}/scripts/graph_utils.py --action add-lesson \
    --graph {REPO_DIR}/mcgs_graph.json \
    --text "run_experiment.py is protected — modify experiment_config.yaml instead"
```

### Step 11.5: Hyperparameter Optimization (Optional, Periodic)

Every `hpo_interval` iterations (default: 10), or when the user requests it, run HPO on the best untuned design:
```bash
python {SKILL_DIR}/scripts/hpo_tune.py \
    --graph {REPO_DIR}/mcgs_graph.json \
    --repo-dir {REPO_DIR} \
    --auto \
    --max-iter {HPO_MAX_ITER} \
    --backend {HPO_BACKEND}
```

Or target a specific node:
```bash
python {SKILL_DIR}/scripts/hpo_tune.py \
    --graph {REPO_DIR}/mcgs_graph.json \
    --node-id {node_id} \
    --repo-dir {REPO_DIR} \
    --max-iter 50 \
    --register
```

HPO creates a new node with tuned hyperparameters (same architecture, better params). The Designer should focus on architecture/algorithm changes and leave hyperparameter tuning to HPO.

### Step 12: Report Progress

After each iteration, tell the user:
- **Iteration N/M**
- **New node**: ID, name, description
- **Objective**: value (and comparison to parent and best-so-far)
- **Status**: improved / regressed / failed
- **Best overall**: node ID, objective value

In multi-objective mode, also report:
- Per-objective scores for the new node
- Consensus score
- Any meta-agent analysis performed this iteration
- Any new objectives generated this iteration

If the new design improved on the best, celebrate briefly. If it regressed, note it as useful information for future iterations.

### Step 13: Continue or Stop

Continue the loop unless:
- Reached max iterations
- User says stop
- You notice the search has converged (last K iterations show no improvement) — suggest stopping and summarize

---

## Phase 3: Summary

When the loop ends (by count, user request, or convergence):

1. **Best design**: Report the best node ID, its objective, and lineage (chain of parents back to node-0)
2. **Improvement**: How much better than baseline? (percent or absolute)
3. **Search statistics**: Total nodes explored, success rate, exploration frontier
4. **Best code diff**: Show `git diff mcgs/node-0..mcgs/node-{best_id}` (the full change from baseline)
5. **Offer to apply**: Ask if the user wants to check out the best design's branch as their working code

In multi-objective mode, also report:
6. **Objective evolution**: How many objectives were generated, which ones had the most influence
7. **Consensus stability**: Did the top-ranked design stay stable across iterations?
8. **Meta-agent insights**: Summary of research phase transitions and key guidance

---

## Important Notes

### On Subagent Spawning
- Use the `Agent` tool to spawn planner, designer, and objective agent subagents
- The planner needs: Read, Bash (for git commands), Grep, Glob tools
- The designer needs: Read, Write, Edit, Bash tools — full code editing
- The objective agent needs: Read tools only (it outputs code, you write the file)
- Set the designer's working directory to the worktree path

### On Git Hygiene
- Never modify the user's main/master branch during MCGS
- All MCGS work happens on `mcgs/node-*` branches
- The `mcgs_graph.json` file is updated on the current working branch
- If something goes wrong, the user can always `git branch -D mcgs/node-*` to clean up

### On Error Handling
- If the designer's code fails to execute (objective script errors), record it as a failed node and move on
- Failed nodes still contribute to the search — they tell the planner what doesn't work
- If multiple consecutive nodes fail, flag this to the user — the objective script might need fixing
- If an objective function fails to load or errors, skip it in consensus and continue

### On the Experiment/Objective Script
- **Single-objective**: script MUST print a float as its last stdout line
- **Multi-objective**: script MUST print valid JSON as its last stdout line
- Scripts should be deterministic (or at least low-variance)
- Scripts should complete within the timeout
- If evaluation is expensive, suggest the user start with a cheap proxy

### On Judging Convergence
- If the last 5+ iterations show no improvement over the best node, the search may have converged
- The exploration term will naturally push toward unexplored regions, but if even those don't help, it's time to stop
- Suggest the user increase `c_puct` if they want more exploration, or decrease it for more exploitation

### On Project Context
- During Phase 1 setup, build a mental model of the project: what it does, how it's structured, what files are modifiable, and what kinds of changes are most promising
- Pass this context to every Planner and Designer subagent prompt. They don't have your context window — they only see what you give them
- Include a "Modifiable files" list and a "Protected files" list in every Designer prompt. The experiment script and evaluation data must never be modified
- As the search progresses, update your project context with lessons learned (e.g., "changes to the loss function have been most effective" or "modifying the data pipeline tends to break things")

### On Multi-Objective Mode
- Start with 2–3 good objectives covering different quality dimensions, and let the system evolve more over time
- The consensus mechanism is self-correcting — bad objectives get near-zero weight automatically
- Meta-agent analysis is the main tool for catching "echo chamber" effects where all objectives measure the same thing
- If all objectives are highly correlated (tau > 0.8), actively direct the Objective Agent toward orthogonal metrics
