# Spawn Objective Agent

{description}

## Steps

1. Spawn a **READ-ONLY** subagent (Agent tool) with this prompt:

```
You are the Objective Agent in a Monte-Carlo Graph Search system.

{guide}

## Research Goal
{research_goal}

## Existing Objectives
{existing_objectives}

## Example Experiment Results
{example_experiment_results}

## Meta-Agent Directions
{meta_agent_directions}

Generate a new objective function. Output:
1. Python code in a fenced ```python block
2. JSON metadata with "name" and "description"
```

2. Extract the Python code and JSON metadata from the subagent's response.

3. Save the Python code to `{objectives_dir}/objective_{next_objective_id}.py`

4. Validate:
```bash
python {scripts_dir}/validate_agent_output.py validate-objective \
    --file {objectives_dir}/objective_{next_objective_id}.py \
    --sample-results '{sample_results_for_validation}'
```

5. If invalid: SendMessage to subagent (1 retry max), then skip if still broken.

6. If valid: register the objective in the graph:
```python
graph.add_objective(name=<name>, filename="objective_{next_objective_id}.py",
                    description=<description>, created_iteration=<current>)
```
Save the graph.

7. Complete this step:
```bash
{complete_command}
```
