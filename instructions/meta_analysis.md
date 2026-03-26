# Meta-Agent Analysis

{description}

You (the orchestrator) perform this directly — NO subagent needed.

## Steps

1. Run consensus verbose to see the tau matrix and weights:
```bash
{consensus_verbose_cmd}
```

2. Review the output:
   - Which objectives agree (high tau)? Which are outliers?
   - Are weights collapsing toward zero for any objective?
   - Are all objectives measuring the same thing (echo chamber)?

3. Read the meta-agent guide for decision framework:
```
{guide}
```

4. Decide on:
   - **research_phase**: exploring / converging / stuck / breakthrough_needed / refining
   - **weight_multipliers**: objective_name -> 0.0-2.0 (1.0 = no change)
   - **weight_adders**: objective_name -> 0.0-0.1 (bypass agreement-based zero)
   - **objective_directions**: guidance for the next Objective Agent

5. Update the graph's meta_state:
   - Load the graph, set `graph.meta_state` fields, save
   - Add a snapshot to `graph.meta_state.history`
   - Apply weights: `graph.apply_meta_weights(...)` and `graph.apply_meta_adders(...)`

6. Recompute consensus and UCB:
```bash
{recompute_consensus_cmd}
{recompute_ucb_cmd}
```

7. Complete this step:
```bash
{complete_command}
```
