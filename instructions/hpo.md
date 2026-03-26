# Hyperparameter Optimization

{description}

## Steps

1. Run the HPO command — this runs **{max_iter} trials** and typically takes 10-30 minutes:
```bash
{command}
```

2. You can monitor progress in real time:
```bash
tail -f {repo_dir}/hpo_progress.log
```

3. When the command finishes, complete with the command output:
```bash
{complete_command}
```

## IMPORTANT: Let HPO finish

- **Do NOT interrupt or kill the process early.** Let all {max_iter} trials complete.
- Early trials are **exploratory by design** — the optimizer (Optuna) samples broadly to map the search space before exploiting promising regions. No improvement in the first 10-20 trials is normal and expected.
- The best hyperparameters are often found in the **second half** of the run, after the sampler has learned the response surface.
- If the process appears stuck, check `hpo_progress.log` — as long as new lines appear, it is still working.
