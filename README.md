# DTLM

This repository contains the `paper_b_sim` simulation code for DTLM.

## Layout

- `paper_b_sim/`: simulation engine, cache policies, evaluation scripts, and tests

## Notes

- Large generated experiment outputs are intentionally excluded from version control.
- The current scripts use local absolute paths in `paper_b_sim/config.py` for the Azure trace data and output directory. Adjust them to your environment before running full experiments.

## Quick checks

From `paper_b_sim/`:

```powershell
python -m unittest tests.test_regressions tests.test_explainability_metrics
```
