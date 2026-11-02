# Opener eval (placeholder)

Offline harness for comparing the legacy opener prompt (`legacy_prompt.py`,
frozen from commit f0d45c4) with the new opener pipeline.

- `profiles.json`: fictional profiles (invented names, generic places, no
  real people). Each has tags (sparse, ambiguous_image, serious, playful,
  mixed) and `expected_absent` words a hallucinating opener might use.
- `render.py`: renders a profile to Hinge-like PNG frames
  (`python -m evals.openers.render --all [--out DIR]`).
- Scores are heuristics, not match rates.

Task 9 completes this document.
