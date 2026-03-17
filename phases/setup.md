# Phase 1: Setup

The user will typically provide a high-level research goal and point you at their codebase. Your job is to understand their project, set up the evaluation infrastructure, and initialize the search. Don't just ask for everything upfront — be proactive: read their code, figure out what metrics matter, and propose a concrete plan.

## 1.1 Understand the Project

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

## 1.2 Decide on Mode and Confirm with User

Based on your understanding, recommend single-objective or multi-objective mode:

- **Single-objective**: when there's one clear metric to optimize and the user knows what it is. Simpler, faster, good for well-defined optimization tasks.
- **Multi-objective** (recommended for research): when the goal is complex, multiple metrics matter, or a single metric could be gamed. The consensus mechanism prevents reward hacking and the Objective Agent discovers evaluation criteria the user might not have thought of.

Confirm with the user:
- Your understanding of the research goal
- The mode (single vs multi-objective)
- How many iterations to run (default: 10–20)
- Timeout per evaluation (default: 300s; adjust based on how long experiments take)

## 1.3 Write the Experiment Script

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

## 1.4 Write Initial Objective Functions (Multi-Objective Only)

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

## 1.5 Initialize the Git Repository and MCGS State

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

## 1.6 Evaluate the Baseline

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

## 1.7 Briefing Before Search

Before starting the loop, give the user a brief summary:
- Baseline metrics (what the unmodified code achieves)
- What the Designer will be allowed to modify (which files, what kinds of changes)
- The search budget (N iterations, estimated time)
- (Multi-objective) The initial objectives and what they measure

Confirm they're ready to start. Then proceed to Phase 2 — read `phases/loop.md`.
