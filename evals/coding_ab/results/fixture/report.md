# Coding memory A/B fixture calibration

Synthetic, planted fixtures with executable tests. No product/default claim.
Solver mode: fixture.

Related: [#71](https://github.com/Rynaro/crystalium/issues/71), [#72](https://github.com/Rynaro/crystalium/issues/72), [#73](https://github.com/Rynaro/crystalium/issues/73), [#74](https://github.com/Rynaro/crystalium/issues/74), [eidolons#635](https://github.com/Rynaro/eidolons/issues/635).

JSON SHA-256: `9f01bfaf1b55691365ee1a29736ac53cb3b0d489daa2e75206a8d88d105fd169`

Paired task bootstrap: 2000 resamples; fixed RNG seed 74.

Token cost and steps are descriptive: **no claim** without their own CIs. Total solver tokens are unavailable in fixture mode. Distraction = paired off success and arm failure / all paired samples.

## 0/4

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 1.000 [1.000, 1.000] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 1.000 [1.000, 1.000] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | 0.000 [0.000, 0.000] | 300.0 | 0.000 | 1.0 | unavailable |
| off | 0.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.0 | unavailable |
| raw | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |

## 0/4:anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 1.000 [1.000, 1.000] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 1.000 [1.000, 1.000] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | 0.000 [0.000, 0.000] | 300.0 | 0.000 | 1.0 | unavailable |
| off | 0.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.0 | unavailable |
| raw | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |

## 0/4:no_anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 1.000 [1.000, 1.000] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 1.000 [1.000, 1.000] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | 0.000 [0.000, 0.000] | 300.0 | 0.000 | 1.0 | unavailable |
| off | 0.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.0 | unavailable |
| raw | 1.000 | 1.000 [1.000, 1.000] | 90.0 | 0.000 | 1.0 | unavailable |

## 1-3/4

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.500, 0.500] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.500, 0.500] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-0.500, -0.500] | 300.0 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |

## 1-3/4:anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.500, 0.500] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.500, 0.500] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-0.500, -0.500] | 300.0 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |

## 1-3/4:no_anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.500, 0.500] | 300.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.500, 0.500] | 84.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-0.500, -0.500] | 300.0 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.500, 0.500] | 90.0 | 0.000 | 1.0 | unavailable |

## 4/4

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.000 [0.000, 0.000] | 280.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.000 [0.000, 0.000] | 78.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -1.000 [-1.000, -1.000] | 280.0 | 1.000 | 1.0 | unavailable |
| off | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| raw | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |

## 4/4:anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.000 [0.000, 0.000] | 280.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.000 [0.000, 0.000] | 78.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -1.000 [-1.000, -1.000] | 280.0 | 1.000 | 1.0 | unavailable |
| off | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| raw | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |

## 4/4:no_anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.000 [0.000, 0.000] | 280.0 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.000 [0.000, 0.000] | 78.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -1.000 [-1.000, -1.000] | 280.0 | 1.000 | 1.0 | unavailable |
| off | 1.000 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 1.0 | unavailable |
| raw | 1.000 | 0.000 [0.000, 0.000] | 84.0 | 0.000 | 1.0 | unavailable |

## all

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.167, 0.833] | 293.3 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.167, 0.833] | 88.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.167, 0.833] | 82.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.167, 0.833] | 60.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-0.833, -0.167] | 293.3 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.167, 0.833] | 88.0 | 0.000 | 1.0 | unavailable |

## anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.000, 1.000] | 293.3 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.000, 1.000] | 88.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.000, 1.000] | 82.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.000, 1.000] | 60.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-1.000, 0.000] | 293.3 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.000, 1.000] | 88.0 | 0.000 | 1.0 | unavailable |

## no_anchor

| Arm | Resolved | Δ vs off [95% CI] | Injected tokens | Distraction | Steps | Total tokens |
|---|---:|---:|---:|---:|---:|---:|
| always_on_k10 | 1.000 | 0.500 [0.000, 1.000] | 293.3 | 0.000 | 1.0 | unavailable |
| always_on_k3 | 1.000 | 0.500 [0.000, 1.000] | 88.0 | 0.000 | 1.0 | unavailable |
| fix_span | 1.000 | 0.500 [0.000, 1.000] | 82.0 | 0.000 | 1.0 | unavailable |
| gated_k3 | 1.000 | 0.500 [0.000, 1.000] | 60.0 | 0.000 | 1.0 | unavailable |
| irrelevant | 0.000 | -0.500 [-1.000, 0.000] | 293.3 | 0.500 | 1.0 | unavailable |
| off | 0.500 | 0.000 [0.000, 0.000] | 0.0 | 0.000 | 0.5 | unavailable |
| raw | 1.000 | 0.500 [0.000, 1.000] | 88.0 | 0.000 | 1.0 | unavailable |
