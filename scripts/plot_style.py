#!/usr/bin/env python3
"""Shared matplotlib style configuration for MCGS visualization scripts.

Provides consistent publication-quality styling across all plotting scripts.
"""

from __future__ import annotations

import matplotlib
matplotlib.use('Agg')  # headless-safe, must be before pyplot import

import matplotlib.pyplot as plt
from pathlib import Path


# ── Color constants ──────────────────────────────────────────────────────────

COLOR_BEST = '#E64B35'       # red — best node highlight
COLOR_BASELINE = '#666666'   # gray — baseline / node-0
COLOR_FAILED = '#cccccc'     # light gray — failed nodes
COLOR_EVALUATED = '#4DBBD5'  # teal — evaluated nodes
MCGS_CMAP = plt.cm.viridis   # default colormap


# ── Style setup ──────────────────────────────────────────────────────────────

def setup_style():
    """Configure matplotlib for publication-quality figures."""
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
        'figure.dpi': 300,
        'axes.labelsize': 20,
        'xtick.labelsize': 14,
        'ytick.labelsize': 14,
        'axes.titlesize': 18,
        'legend.fontsize': 14,
        'lines.linewidth': 2,
        'lines.markersize': 8,
        'savefig.bbox': 'tight',
        'savefig.pad_inches': 0.05,
        'savefig.facecolor': 'white',
    })


# ── Save helper ──────────────────────────────────────────────────────────────

def save_figure(fig, output_dir: Path, stem: str):
    """Save figure as both PNG (300 DPI) and PDF, then close."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f'{stem}.png', dpi=300, facecolor='white')
    fig.savefig(output_dir / f'{stem}.pdf', facecolor='white')
    print(f"Saved {stem}.png and {stem}.pdf to {output_dir}")
    plt.close(fig)
