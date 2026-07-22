"""Champion-seeded neuroevolution for Breakout.

A torch-free genetic algorithm that treats the DQN's MLP weights as a genome and
optimizes the *real* game score directly (no gradients, no reward shaping). It
reuses the exact network shape the app plays (`app/rl_policy.py`) and the exact
headless physics the trainer evaluates (`rl.breakout_env`), so an evolved genome
behaves identically when exported to the arena.

The engine never touches the deployed champion in ``rl/policy/`` — it publishes
to a separate ``rl/policy_evo/`` slot, and a winner must clear the same paired
200-game audit as any DQN champion before it can be promoted.
"""
