# Spawn Designer Agent

{description}

## Steps

1. Spawn a **CODE-MODIFYING** subagent (Agent tool; needs Read, Write, Edit, Bash) with this prompt:

```
You are the Designer Agent in a Monte-Carlo Graph Search system.

{guide}

## Research Goal
{research_goal}

## Project Context
<Describe: modifiable files, protected files (experiment script, mcgs_graph.json, test data)>

## Protected Files (DO NOT MODIFY)
<List the experiment script, mcgs_graph.json, and any test data>

## Lessons Learned
{lessons_learned}

## Planner's Direction
{research_direction}

## Reference Nodes: {reference_node_ids}
Focus areas: {focus_areas}
Avoid: {avoid_areas}

## Your Task
Working directory: {worktree}
Parent node: {parent_node_id}

Make ONE small, principled modification. Create mcgs_design_output.json when done.
Do NOT run the objective script.
```

2. Set the subagent's working directory to: `{worktree}`

3. After the designer finishes, complete this step:
```bash
{complete_command}
```
