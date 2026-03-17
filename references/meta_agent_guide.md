# Meta-Agent Analysis Guide

This reference describes how the orchestrator (you, Claude) performs meta-agent analysis during the MCGS loop. The meta-agent provides high-level oversight of research progress and guides the evolution of objective functions.

## When to Run

Run meta-agent analysis every `meta_interval` iterations (default: 10). This is configured in `mcgs_graph.json` under `config.meta_interval`.

## What to Analyze

Before making decisions, review this data from the latest `consensus.py` output:

1. **Kendall tau correlation matrix**: Shows which objectives agree/disagree on design quality. High correlation (> 0.7) between many pairs means objectives are redundant. Negative correlations flag potentially misleading objectives.

2. **Objective weights**: The consensus-derived weights. Low-weight objectives are being suppressed because they disagree with the majority.

3. **Top designs**: Which designs rank best under consensus vs under individual objectives. Disagreement here reveals where objectives diverge.

4. **Progress trend**: Are recent designs improving? Has the search plateaued? Are failures increasing?

## Decisions to Make

### 1. Research Phase Assessment

Classify the current phase:
- **exploring**: early stage, few designs, wide search
- **converging**: designs improving steadily, clear direction emerging
- **stuck**: no improvement for several iterations despite exploration
- **breakthrough_needed**: marginal gains only, need a fundamentally different approach
- **refining**: near-optimal, small tweaks only

### 2. Weight Adjustments

For each active objective, you have two controls:

**Weight multiplier** (0.0 to 2.0) — scales the agreement-based weight:
- **1.0** (default): no change
- **> 1.0**: amplify this objective's influence (it captures something valuable that others miss)
- **< 1.0**: reduce influence (it's redundant or slightly misleading)
- **0.0**: effectively disable (it's clearly harmful)

**Weight adder** (0.0 to 1.0) — bypasses the agreement-based zero:
- **0.0** (default): no override; the consensus mechanism decides
- **> 0.0**: guarantees this objective gets at least this much influence, even if negatively correlated with others. Use this to rescue objectives that the consensus suppresses but that you believe capture a genuinely important quality dimension.

The formula is: `weight = agreement × age_decay × multiplier + adder`

Be conservative. Only adjust weights when you see clear evidence. The consensus mechanism handles most cases through its agreement-based weighting. Use the adder only when you have strong strategic reasons to override the consensus.

### 3. Objective Directions

Write strategic guidance for the next Objective Agent invocation:
- What quality dimension is missing from the current objective set?
- Are all objectives looking at the same metrics? (echo chamber risk)
- Should the next objective focus on robustness? generalization? efficiency? something else?
- Are there metrics in the experiment results that no objective currently uses?

## Output Format

Store your analysis in `graph.meta_state` as a JSON structure:

```json
{
    "research_phase": "converging",
    "research_assessment": "Designs 12-18 show steady improvement in scaling exponent. Objectives 0-2 are highly correlated (tau > 0.85), suggesting we need more diverse evaluation criteria.",
    "objective_directions": "Generate an objective that focuses on memory efficiency rather than runtime scaling. Current objectives all measure variants of step count.",
    "weight_adjustments": {
        "scaling_exponent": 1.2,
        "raw_runtime": 0.5,
        "step_efficiency": 1.0
    },
    "weight_adders": {
        "frustration_robustness": 0.1
    }
}
```

The orchestrator will save this to `mcgs_graph.json` and pass `objective_directions` to the next Objective Agent.

## Key Principles

- **Don't micromanage**: the consensus mechanism is self-correcting for most objective issues. Focus on strategic direction.
- **Watch for echo chambers**: if all objectives measure similar things, the consensus provides a false sense of robustness. This is the most important failure mode to catch.
- **Trust negative tau**: an objective negatively correlated with the consensus is almost certainly misleading. Set its weight to 0.0.
- **Phase awareness**: different phases need different objective diversity. Early exploration benefits from diverse objectives; late refinement benefits from focused ones.
