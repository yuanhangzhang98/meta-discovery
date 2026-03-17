# Meta-Discovery: Automated Scientific Research as Meta-Optimization

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill that turns Claude into an automated research system. It combines **Monte-Carlo Graph Search (MCGS)** for exploring a codebase's design space with **consensus objective aggregation** that co-evolves evaluation criteria alongside solutions — treating scientific discovery as **meta-optimization**.

Based on the paper:

> **Scientific discovery as meta-optimization: a combinatorial optimization case study**
> Zhang, Y.-H., Sipling, C., & Di Ventra, M. (2026).
> [https://www.researchsquare.com/article/rs-9108409/v1](https://www.researchsquare.com/article/rs-9108409/v1)

Original codebase: [yuanhangzhang98/LLM_meta_optimization](https://github.com/yuanhangzhang98/LLM_meta_optimization)

## Disclaimer

This skill was created using Claude's official [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) skill by directly feeding in the paper and the original codebase. It is **still under extensive testing and may change significantly**.

## How It Works

The skill orchestrates four specialized LLM agents in an iterative loop:

1. **Meta-Agent** — steers the research strategy; analyzes objective correlations and adjusts weights to prevent echo-chamber effects
2. **Objective Agent** — generates proxy objective functions capturing different aspects of solution quality; aggregated into a consensus ranking via Kendall's tau correlation-weighted voting with age decay
3. **Planner** — uses MCGS (UCB-based exploration-exploitation) over the design graph to propose strategic research directions
4. **Designer** — implements one focused code modification per iteration for clean causality

All designs are stored as git branches (`mcgs/node-*`) forming a DAG.

Supports two modes: **single-objective** (one evaluation script outputs a float) and **multi-objective** (experiment script outputs JSON metrics; multiple co-evolving objectives with consensus aggregation).

## Installation

1. Install [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
2. Clone this repository into your Claude Code skills directory:
   ```bash
   # On macOS/Linux: ~/.claude/skills/
   # On Windows: %USERPROFILE%\.claude\skills\
   git clone <this-repo-url> ~/.claude/skills/meta-discovery
   ```
3. Navigate to the project you want to optimize and invoke the skill in Claude Code.

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- Python 3.8+
- Git
- NumPy, SciPy (for consensus aggregation)

## License

MIT License. See [LICENSE](LICENSE) for details.

## Citation

If you use this skill in your research, please cite the original paper:

```bibtex
@article{zhang2025scientific,
  title={Scientific discovery as meta-optimization: a combinatorial optimization case study},
  author={Zhang, Yuan-Hang and Sipling, Chesson and Di Ventra, Massimiliano},
  journal={Research Square preprint},
  year={2026},
  doi={10.21203/rs.3.rs-9108409/v1}
}
```

## Acknowledgments

- Paper: [Scientific discovery as meta-optimization](https://www.researchsquare.com/article/rs-9108409/v1) by Zhang, Sipling, & Di Ventra (2026)
- Original implementation: [LLM_meta_optimization](https://github.com/yuanhangzhang98/LLM_meta_optimization)
- Skill creation: [Claude skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
