# Collision Detection Agent — Implementation Deep-Dive
### Orbital Guardian, Agent 3 of 10

This is the engineering-level implementation guide for **your** module. It assumes the contracts already fixed in `Orbital_Guardian_Build_Plan.md` (§3) and expands the "filter cascade" one-liner into something you can actually code, test, and defend in front of judges.

---

## 1. Restating the job precisely

You receive a pile of ephemerides (one per tracked object, each a list of `{t, r, v}` samples over a 24 h window at 60 s resolution) from the Prediction Agent. For **N** objects that's **N·(N-1)/2** possible pairs — at N=50 that's 1,225 pairs, at N=500 that's ~125,000. Checking every pair at every one of ~1,440 timesteps (N²·T) is the naive approach and it is exactly what real systems avoid.

Your job is a **funnel**: start with all pairs, cheaply discard the overwhelming majority that geometrically cannot conjunct, and spend expensive precise computation only on the few that survive. The demo target in the build plan ("Screened 1,225 possible pairs → 3 candidates flagged") *is* the point — the funnel itself is the deliverable, not just the final answer.

There are two legitimate architectures for this. The build plan mentions both; here's how to actually choose and build one.

---

## 2. Architecture choice: classical filter cascade vs. KD-tree

### 2.1 The classical three-filter cascade (the "real" CARA/SOCRATES pattern)

This is the literal algorithm described in Alfano & Finkleman's *Operating Characteristic Approach to Effective Satellite Conjunction Filtering* — the paper your build plan's filter-cascade citation traces back to. Three filters, applied in cheapest-first order:

**Filter 1 — Apogee/Perigee filter (orbit-shape only, no geometry)**
Two orbits can only possibly intersect in radius if their altitude bands overlap at all. For each object compute:
```
r_apogee  = a * (1 + e)
r_perigee = a * (1 - e)
```
from the mean elements (semi-major axis `a`, eccentricity `e`). A pair `(i, j)` survives only if their `[r_perigee, r_apogee]` bands overlap, usually with a safety pad (e.g. ±5–10 km to account for perturbation/uncertainty):
```
overlap = (r_perigee_i - pad) <= (r_apogee_j + pad) and (r_perigee_j - pad) <= (r_apogee_i + pad)
```
This is O(N log N) if you sort objects by perigee and sweep, or O(N²) brute-force (still cheap since it's just scalar comparisons, no vectors). For your synthetic LEO fleet (500–600 km, mostly circular) this filter alone will eliminate almost nothing — most of your objects are in the same altitude band by design — so don't expect this stage to do much work in the hackathon demo. It matters much more on a real mixed-catalog scale (LEO vs MEO vs GEO).

**Filter 2 — Orbit-path (geometric) filter**
Eliminates pairs whose orbital *planes/paths* never come close, independent of timing — i.e., even ignoring where each satellite currently is along its orbit, could the two 3D ellipses ever pass within some threshold distance of each other? This is more expensive: it typically involves computing the minimum distance between the two orbital curves as geometric objects (not propagated trajectories). For a hackathon, a good-enough approximation: sample each orbit's ellipse at, say, 100 evenly-spaced true-anomaly points (cheap — one Kepler-orbit evaluation, not SGP4), and check the minimum pairwise distance between the two point clouds against a threshold (e.g. 50–100 km). This step is what actually kills off most of your object pairs, because it accounts for inclination/RAAN separation, not just altitude.

**Filter 3 — Time filter**
For the pairs that survive both geometric filters, restrict fine-grained checking to the specific time windows where both objects are actually near their orbits' mutually-closest points — you don't need to check every 60 s timestep across the full 24 h window, only the handful of windows around each orbit-crossing opportunity. This is where you use the orbital periods to predict *when* each object revisits the geometrically-close region, and only propagate/compare densely inside those windows.

**Filter 4 — Precise TCA solve** (see §3)

**Trade-off:** this pipeline is the textbook/CARA-faithful approach and is a strong "we implemented the real algorithm, not a shortcut" pitch point. It is also the most implementation work, and Filter 2 in particular carries real geometric subtlety.

### 2.2 The KD-tree pattern (recommended for hackathon timelines)

This is the pattern from the open-source `mooneedg/Starlink_Project` referenced in your build plan and research doc. Instead of reasoning about orbit shapes, you reason directly about the **propagated position cloud** you already have from the Prediction Agent:

1. At each timestep `t_k`, you already have `r_km` for every object (that's literally the Prediction Agent's ephemeris output).
2. Build a `scipy.spatial.KDTree` over all objects' positions **at that timestep**.
3. Query the tree for all pairs within some generous threshold radius (e.g. 100 km) — this is a single `KDTree.query_pairs(r=threshold)` call, and it is the part that replaces filters 1+2+3 above in one step.
4. Union the flagged pairs across all timesteps into a candidate set.
5. Run the precise TCA solve (§3) only on that candidate set, restricted to a time window around whichever timestep(s) triggered the flag.

**Why this is the right call for you specifically:**
- It's *correct by construction* — you're literally checking real 3D distances, not a proxy — so there's no risk of a subtle geometric-filter bug silently dropping a real conjunction (a real risk with Filter 2 above if you get the ellipse-sampling resolution wrong).
- `scipy.spatial.KDTree.query_pairs` is a single, well-tested library call — orders of magnitude less code than implementing Filter 2 correctly.
- Complexity is `O(T · N log N)` for tree-build + query per timestep instead of `O(N²)`, which is exactly the same asymptotic win the classical cascade gives you, just achieved spatially rather than orbit-analytically.
- It's the literal open-source prior-art pattern cited in your own research doc, so you can honestly say "we implemented the pattern used by \[Starlink_Project], validated conceptually against SOCRATES' approach."

**The honest trade-off to state to judges:** the KD-tree approach is a *spatial* screen, not an *orbital* one — it will not naturally distinguish "these two objects happen to be near each other at t=500s but neither will return to this region" from "these two objects orbit-cross regularly." In practice this doesn't matter for a single 24h screening window (which is your Prediction Agent's actual output), but it's worth a sentence in your writeup so it reads as "chose the KD-tree approach deliberately" rather than "didn't know about the classical filters."

**Recommendation: build the KD-tree version.** It's the one your build plan's "Suggested tech" line points to (`scipy.spatial.KDTree`), it matches the demo target's tight timeline, and it is real, correct, published practice — not a toy shortcut.

---

## 3. Precise TCA (Time of Closest Approach) solve

Once a pair is flagged as a candidate by either filter path, you need the *exact* minimum separation and the *exact* time it occurs — the coarse detection grid (60 s steps) is not precise enough for the Risk Assessment Agent's Pc computation downstream, which needs the true geometry at TCA.

### 3.1 The math

Define the relative position vector between the two objects as a function of time:
```
d(t) = r_secondary(t) - r_primary(t)
```
The squared separation distance is `D(t) = |d(t)|² = d(t)·d(t)`. Closest approach occurs where the derivative of distance is zero:
```
dD/dt = 2 · d(t) · ḋ(t) = 0
```
where `ḋ(t) = v_secondary(t) - v_primary(t)` is the relative velocity. So TCA is the root of:
```
f(t) = d(t) · ḋ(t) = 0
```
This is a classic 1D root-find. Two practical ways to solve it:

**Option A — Newton-Raphson on the derivative condition**
```python
def relative_state(t, obj_a_interp, obj_b_interp):
    ra, va = obj_a_interp(t)
    rb, vb = obj_b_interp(t)
    d = rb - ra
    ddot = vb - va
    return d, ddot

def f(t, ...):
    d, ddot = relative_state(t, ...)
    return np.dot(d, ddot)

def fprime(t, ...):
    # f'(t) ≈ |ddot|^2 + d·d̈  — the acceleration term d̈ is usually small/negligible
    # over the short bracket window, so a good practical approximation is:
    d, ddot = relative_state(t, ...)
    return np.dot(ddot, ddot)

# Newton-Raphson from a good initial guess (the coarse-grid minimum)
t = t_coarse_min
for _ in range(20):
    t_new = t - f(t, ...) / fprime(t, ...)
    if abs(t_new - t) < 1e-3:  # seconds
        break
    t = t_new
```
You need continuous position/velocity functions of `t`, not just the 60 s samples — interpolate. A cubic (Hermite) spline through the position **and** velocity samples you already have from the Prediction Agent (it gives you both `r` and `v` at each step, which is exactly what a Hermite interpolant wants) gives you a smooth, accurate `r(t)`, `v(t)` you can evaluate at arbitrary sub-second `t`. This avoids re-running SGP4 during the root-find, which is both slower and introduces frame-conversion overhead you don't want inside an optimization loop.

**Option B — Vectorized fine-grained scan (simpler, still fast enough)**
Skip the calculus. Around the coarse-grid minimum, resample at much finer resolution (e.g. 0.1–1 s) using the same interpolants, compute `|d(t)|` at every sample with plain vectorized NumPy, and take the `argmin`:
```python
t_fine = np.arange(t_coarse_min - 60, t_coarse_min + 60, 0.5)  # ±60s window, 0.5s steps
d_fine = interp_rb(t_fine) - interp_ra(t_fine)                  # vectorized
dist_fine = np.linalg.norm(d_fine, axis=1)
tca_idx = np.argmin(dist_fine)
tca_t, miss_distance = t_fine[tca_idx], dist_fine[tca_idx]
```
This is less "textbook" than Newton-Raphson but is easier to get right, easy to unit-test (it can't diverge or oscillate the way a bad Newton step can), and is fast enough at this scale (only run on your small candidate set, not the full pair set). **Given your timeline, Option B is the pragmatic choice; mention Option A in your writeup as the "more precise" method you're aware of and could swap in.**

### 3.2 From TCA back to your output schema

Once you have `tca_t` and the interpolated states at that instant, populating your contract output is direct:
```python
d_tca  = interp_rb(tca_t) - interp_ra(tca_t)          # relative_position_km
v_tca  = interp_vb(tca_t) - interp_va(tca_t)          # relative_velocity_vec_kms
miss_distance_km = np.linalg.norm(d_tca)
relative_velocity_kms = np.linalg.norm(v_tca)
```
This maps exactly onto the schema your build plan already specifies:
```json
{
  "primary_id": "SAT-042",
  "secondary_id": "DEB-891",
  "tca_utc": "2026-09-12T14:32:00Z",
  "miss_distance_km": 1.8,
  "relative_velocity_kms": 8.2,
  "relative_position_km": [1.1, -0.9, 0.6],
  "relative_velocity_vec_kms": [7.9, -1.8, 1.2]
}
```
One subtlety worth handling explicitly: **which pair is "primary" vs "secondary"?** A sensible convention: the object with `object_type: PAYLOAD` (from the Tracking Agent) is primary when the pair is PAYLOAD-vs-DEBRIS or PAYLOAD-vs-ROCKET_BODY; for PAYLOAD-vs-PAYLOAD, either convention is fine as long as it's consistent (e.g. alphabetical `object_id`) since downstream agents treat both symmetrically until the Negotiation Agent.

---

## 4. Reference implementation sketch

```python
import numpy as np
from scipy.spatial import KDTree
from scipy.interpolate import CubicHermiteSpline

SCREENING_THRESHOLD_KM = 100.0   # generous KD-tree flag radius
FINE_WINDOW_S = 120               # ± window around a flagged timestep for TCA refine
FINE_STEP_S = 0.5

def build_interpolants(ephemeris):
    """ephemeris: list of {t_utc, r_km, v_kms} -> (r_interp(t), v_interp(t)) as functions of seconds-since-epoch"""
    t = np.array([to_seconds(e["t_utc"]) for e in ephemeris])
    r = np.array([e["r_km"] for e in ephemeris])
    v = np.array([e["v_kms"] for e in ephemeris])
    # Hermite spline per axis using position AND velocity samples
    splines_r = [CubicHermiteSpline(t, r[:, k], v[:, k]) for k in range(3)]
    r_interp = lambda tt: np.array([s(tt) for s in splines_r]).T
    v_interp = lambda tt: np.array([s.derivative()(tt) for s in splines_r]).T
    return r_interp, v_interp

def coarse_screen(ephemerides, threshold_km=SCREENING_THRESHOLD_KM):
    """Step through timesteps, KD-tree flag close pairs, union candidates."""
    n_steps = len(ephemerides[0]["ephemeris"])
    candidates = {}  # (i, j) -> list of flagged timestep indices

    for k in range(n_steps):
        positions = np.array([obj["ephemeris"][k]["r_km"] for obj in ephemerides])
        tree = KDTree(positions)
        pairs = tree.query_pairs(r=threshold_km)
        for (i, j) in pairs:
            candidates.setdefault((i, j), []).append(k)

    return candidates

def refine_tca(obj_a, obj_b, flagged_indices):
    """Vectorized fine-grained scan around each flagged region; return best (t, miss_km, ...) per contiguous cluster."""
    r_a, v_a = build_interpolants(obj_a["ephemeris"])
    r_b, v_b = build_interpolants(obj_b["ephemeris"])

    t_flagged = [to_seconds(obj_a["ephemeris"][k]["t_utc"]) for k in flagged_indices]
    # cluster contiguous/near-contiguous flagged timesteps into windows so one close pass
    # isn't refined N times (see note below)
    windows = cluster_into_windows(t_flagged, gap_s=180)

    results = []
    for (t_lo, t_hi) in windows:
        t_fine = np.arange(t_lo - FINE_WINDOW_S, t_hi + FINE_WINDOW_S, FINE_STEP_S)
        d = r_b(t_fine) - r_a(t_fine)
        dist = np.linalg.norm(d, axis=1)
        idx = np.argmin(dist)
        tca_t = t_fine[idx]
        d_tca = r_b(tca_t) - r_a(tca_t)
        v_tca = v_b(tca_t) - v_a(tca_t)
        results.append({
            "tca_utc": to_iso(tca_t),
            "miss_distance_km": float(np.linalg.norm(d_tca)),
            "relative_velocity_kms": float(np.linalg.norm(v_tca)),
            "relative_position_km": d_tca.tolist(),
            "relative_velocity_vec_kms": v_tca.tolist(),
        })
    return results

def run_collision_detection(request):
    ephemerides = request["ephemerides"]
    candidates = coarse_screen(ephemerides)
    conjunctions = []
    n_pairs_screened = len(ephemerides) * (len(ephemerides) - 1) // 2

    for (i, j), flagged_indices in candidates.items():
        for result in refine_tca(ephemerides[i], ephemerides[j], flagged_indices):
            conjunctions.append({
                "primary_id": ephemerides[i]["object_id"],
                "secondary_id": ephemerides[j]["object_id"],
                **result,
            })

    return {
        "conjunctions": conjunctions,
        "n_pairs_screened": n_pairs_screened,   # for your "1,225 → 3" funnel counter
    }
```

**Note on clustering flagged timesteps:** if two objects stay within your 100 km threshold for several consecutive 60 s samples (which they will, near a close approach), don't treat each flagged timestep as a separate conjunction — cluster contiguous flagged indices into one window and refine once. The `cluster_into_windows` helper is a one-liner (sort the flagged times, split into groups wherever the gap between consecutive flagged times exceeds some tolerance, e.g. 3 minutes). This also correctly handles the rare case of two objects with **two separate** close approaches in the same 24 h window (e.g. a near-circular LEO pair that repeats its relative geometry every orbit) — they'll form two separate clusters and you'll correctly emit two conjunction records.

---

## 5. Complexity & performance notes (for your demo narrative)

| Stage | Cost | Notes |
|---|---|---|
| KD-tree build+query per timestep | `O(N log N)` | Repeated per timestep (`T` times) → `O(T·N log N)` total |
| Candidate refinement (TCA solve) | `O(1)` per candidate, tiny constant | Only runs on the handful of survivors, not all pairs |
| **Naive brute-force baseline** | `O(N² · T)` | What you're explicitly avoiding — this is the number to quote as "the naive approach we didn't take" |

At N=50, T=1,440 (24h @ 60s): brute force is ~50 million distance evaluations; the KD-tree approach does 1,440 tree builds over 50 points each (trivial) plus queries — this is genuinely near-instant, which supports the demo target's "within a few seconds" claim even before any optimization. At full-catalog scale (SOCRATES screens ~10,000+ payloads against 24,000+ tracked objects daily), this is exactly the regime where the classical filter cascade or a coarser KD-tree grid (e.g. every 5 min instead of every 60 s for the first pass) becomes necessary — worth a sentence in your writeup as "here's how this scales past hackathon size," which signals you understand the production path without needing to build it.

---

## 6. Correctness pitfalls specific to this agent

1. **Frame consistency.** The Prediction Agent's contract already promises `frame: "ECI_J2000"` — confirm this at the ingestion boundary of your agent (assert it, don't just trust it) since a TEME/ECI mismatch will silently produce wrong distances that still *look* plausible. This is called out in your build plan as "the single most common correctness bug in student projects" for the Prediction Agent, but it's your agent's job to catch it if it slips through.
2. **Threshold too tight → missed real conjunctions.** Your 100 km KD-tree screening threshold is a **pad**, not the actual danger threshold (which is meters, from the HBR in the Risk Assessment Agent). Set it generously — false positives here are cheap (they just mean a few extra TCA refines), false negatives are a real bug (a missed collision). Validate this by checking that your synthetic-fleet's deliberately-conjuncting pairs *do* get flagged well before their actual TCA.
3. **Self-pairs and duplicate objects.** Make sure `query_pairs` isn't somehow flagging an object against itself, and that your Tracking Agent's `object_id` uniqueness is enforced upstream (its own demo-target success criterion) — a duplicated ID would silently corrupt your pair indexing.
4. **Sample-rate aliasing.** A 60 s coarse grid can, in principle, "step over" an extremely fast, extremely close encounter (very high relative velocity + very small threshold) if two objects pass fully through the screening radius between two samples. At your synthetic-fleet relative velocities (~7–8 km/s is typical for near-polar-vs-near-polar LEO crossings) and 100 km threshold, the pass takes tens of seconds — a 60 s grid is right at the edge. **Practical fix:** either tighten the coarse grid to 10–30 s for the KD-tree pass, or (cheaper) pad the KD-tree threshold further (e.g. 150–200 km) to build in margin against this aliasing risk. Worth a unit test: construct a synthetic pair with a known, very fast, very close pass and confirm your coarse grid still catches it.
5. **Multiple conjunctions per pair, per window.** Already handled by the windowing/clustering logic above — don't assume one pair yields at most one conjunction record.

---

## 7. Validation & demo plan

1. **Unit test — synthetic exact case.** Construct two objects with hand-computed circular orbits (skip SGP4 entirely) where you can derive the analytic TCA and miss distance by hand or with a simple closed-form check. Confirm your Newton-Raphson/fine-scan solver reproduces it to sub-meter precision.
2. **Integration test — the funnel demo itself.** Generate the 50–100 object synthetic fleet (owned by the Tracking Agent, per the build plan) with 2–3 objects deliberately placed on near-identical orbital planes with a small RAAN/mean-anomaly offset so they're guaranteed to pass close within the 24 h window. Run the full pipeline and confirm:
   - The screened-pairs counter matches `N·(N-1)/2` exactly (e.g. 1,225 for N=50).
   - Only the deliberately-placed conjuncting pairs appear in the output — check for false positives (noise) as carefully as false negatives.
3. **Sanity-check against SOCRATES' published methodology** (not the live data — that's out of scope for a synthetic fleet, but the *pattern*): confirm your filter ordering and thresholding logic conceptually matches the apogee/perigee → orbit-path → time → TCA sequence CelesTrak documents, even though you're implementing the KD-tree analog of stages 1–3. This is the credibility line for judges who know the field: "we didn't reinvent this, we implemented the pattern used by \[SOCRATES / Starlink_Project]."
4. **Performance demo.** Time the full run and display it — "screened 1,225 pairs in **X ms**" is a concrete, visible number for the dashboard's funnel counter and directly supports the build plan's demo target.

---

## 8. What NOT to do here

- **Don't** run SGP4 or any propagator inside this agent — you receive ephemerides already propagated by the Prediction Agent. Re-propagating here duplicates work and breaks the JSON-contract independence the whole architecture depends on.
- **Don't** compute Pc or risk tiers here — that's the Risk Assessment Agent's job. Your output is purely geometric (TCA, miss distance, relative velocity) with zero notion of "risk" or object size.
- **Don't** filter out pairs based on object type (e.g. skipping DEBRIS-DEBRIS pairs) unless your build plan explicitly says to scope it that way — a debris-on-debris collision is still a valid, catalog-relevant conjunction and cascades matter for the Kessler-syndrome framing in your pitch.

---

## Sources used in this deep-dive

- Alfano, S., & Finkleman, D., *Operating Characteristic Approach to Effective Satellite Conjunction Filtering* (AGI/AAS) — the primal source for the apogee/perigee, orbit-path, and time filter definitions.
- CelesTrak SOCRATES methodology documentation (celestrak.org/SOCRATES) — daily full-catalog screening scale reference.
- `mooneedg/Starlink_Project` (GitHub) — the KD-tree + vectorized NumPy TCA-solver pattern this guide's recommended architecture follows.
- `scipy.spatial.KDTree` / `scipy.interpolate.CubicHermiteSpline` documentation — the two library primitives the reference implementation is built on.
- Your own `Orbital_Guardian_Build_Plan.md` §3 and `Orbital_Guardian_Research_Reference.md` §2 — the binding contracts this implementation targets.
