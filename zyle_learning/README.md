# Zyle Learning

`zyle_learning` is an isolated, pixel-observation Breakout reinforcement-learning project. It has its
own virtual environment and contains no runtime import from `rl/` or `app/`. The built-in level data and
collision behavior are ports; the older projects are not dependencies.

The goal is zero-shot layout generalization. A high score on a training board is only a diagnostic. The
acceptance metrics are mean score, board clear rate, and bricks-cleared fraction on built-in level 4
(held out from the default training distribution) and on fixed seeds that create fresh procedural boards.

## Setup

From `zyle_learning/` in PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\venv\Scripts\python.exe -m pytest
```

The pinned Torch build targets CUDA 12.6, matching the local RTX setup. If this is reproduced on a host
with a different CUDA runtime, change only the Torch wheel/index together; do not install into either of
the other project environments.

Run a random-policy environment smoke test:

```powershell
.\venv\Scripts\python.exe -c "import gymnasium as gym, zl; e=gym.make('ZyleBreakout-v0'); o,i=e.reset(seed=1); print(o.shape, e.step(e.action_space.sample())[1:])"
```

## Training and evaluation

M1, level 1 only:

```powershell
.\venv\Scripts\python.exe -m zl.train --level-mode fixed --fixed-level 1 --levels-per-episode 1 --run-dir runs/m1_level1
```

M2/M3 mixed training (procedural boards plus built-ins 1–3; level 4 cannot be sampled):

```powershell
.\venv\Scripts\python.exe -m zl.train --run-dir runs/generalization
```

Evaluate the final PPO checkpoint on both generalization suites:

```powershell
.\venv\Scripts\python.exe -m zl.evaluate --model runs/generalization/checkpoints/zyle_ppo_final.zip
```

Run only the held-out built-in or establish chance baselines:

```powershell
.\venv\Scripts\python.exe -m zl.evaluate --held-out
.\venv\Scripts\python.exe -m zl.evaluate --baseline random
.\venv\Scripts\python.exe -m zl.evaluate --baseline noop
```

TensorBoard logs are written below each run directory. Checkpoints record optimizer and policy state.
Evaluation seeds are constants in `zl/config.py`, are never training seeds, and produce repeatable reports.

## Three design decisions

### 1. SB3 PPO primary; DQN is a comparison

SB3 is the pragmatic choice over CleanRL here because the research risk is the environment distribution
and generalization measurement, not reimplementing rollout storage, checkpointing, multiprocessing, and
logging. PPO is primary because eight independent procedural environments provide exactly the diverse,
on-policy batches PPO uses well, while clipped updates are less brittle when board sizes and reward scales
change. DQN remains available through `--algorithm dqn` as a useful sample-efficiency comparison, but its
replay buffer mixes substantially different board distributions and makes stale-distribution effects an
extra variable. CleanRL would become attractive only if a later experiment needs algorithm internals SB3
cannot expose.

### 2. 96×96, seven semantic channels, four frames, pure pixels

Each frame contains live-brick occupancy, remaining durability, Piercer cells, Splitter cells, paddle,
balls, and a global Piercer-duration plane. Four frames are concatenated channel-first, yielding a
`28×96×96` uint8 observation. This makes direction and speed observable without privileged coordinates,
keeps every source grid—from 10×6 to 48×24—the same shape, and makes special mechanics visually explicit.
The custom CNN starts with a 5×5 stride-2 convolution, and rendered balls have a minimum two-pixel radius,
because an Atari-style 8×8 stride-4 front end can erase the precision signal. A CNN-plus-state dictionary
is intentionally not primary: exact coordinates can dominate learning and weaken the claim that layout-
general visual control works. It is a controlled fallback only if the pure-pixel learning curve fails.

### 3. A mixture of shape priors, including scale tails

The procedural generator samples four families: independent sparse/dense masks, bilateral/optional vertical
symmetry, cellular blobs, and line/stroke glyphs. It separately samples dimensions, density, durability,
and power-up placement. Twenty percent of boards come from a 30–48 column by 14–24 row large/dense branch;
without that branch, holding out the 48×24 tribute would be a fake test because its scale would never occur
during training. These priors cover disconnected art, logos/text, connected regions, mirrored arcade walls,
and noisy future layouts without trying to imitate the held-out picture. Generator families and all random
choices are seedable; evaluation uses fresh seeds fixed only for repeatability.

## Physics parity and current milestone status

`tests/fixtures/physics_golden.json` was captured once from the proven level-one simulator. It contains
three seeds, different action programs, ball/paddle state, speed, score, lives, and per-brick hit state at
collision and life-loss checkpoints. `test_physics_parity.py` loads only that JSON and the new standalone
simulator. It never imports the old project. Separate tests cover durability, Piercer, Splitter, exact level
dimensions/types, generator determinism, held-out leakage rejection, and fixed observation shape.

M0–M3 environment, training, and evaluation infrastructure is implemented. This repository does **not**
claim M1 learning or M3 competence until the GPU runs have produced checkpoints and their reported metrics
beat the checked random/no-op baselines. Generalization success is an empirical result, not something the
code scaffold can assert.

