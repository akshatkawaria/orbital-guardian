# Integration notes

## Scope

This assembly integrates the ten supplied agent ZIP files. Their original bytes are
preserved in `original_archives/`. Runtime adaptations are in `app/integration/adapters.py`.
The root application imports contributor namespaces explicitly to avoid collisions between
the several independently supplied packages named `app`.

## Changes made

1. Replaced Module 10's placeholder registry with adapters calling contributor implementations.
2. Connected the Tracking Agent's synthetic fleet generator to the Prediction Agent's
   SGP4/Skyfield implementation. Preserved fractional timestamps and full numerical precision
   in ephemerides; plotting and tables format values only at display time.
3. Constructed the seeded encounter by adjusting synthetic secondary mean elements with
   a numerical optimizer against SGP4 positions. The warning is then independently detected
   from the propagated paths; it is not a timed UI script.
4. Enabled coarse-screen padding in the Collision Agent. Applied a 20 km reporting threshold
   after refinement, rather than treating all padded candidates as conjunctions. Module 10
   stores one conjunction per pair; the adapter explicitly retains the closest event.
5. Fixed Risk Agent relative imports and ISO timestamp handling. The original series failed
   the zero-miss isotropic Gaussian analytical check. Its public `chan_pc` function now
   integrates the Gaussian over the hard-body disk in principal axes. Labels identify the
   method honestly; the original series code remains unused for historical comparison.
6. Fixed Agent 5's improvement calculation to use its computed no-burn baseline.
   The integration adapter adds the difference between its maneuvered and nominal two-body
   solutions to the SGP4 trajectory. It searches for a new closest-approach time rather than
   evaluating separation only at the old encounter time. This avoids mistaking SGP4/two-body
   model disagreement for maneuver benefit.
7. Mapped Agent 5 candidate IDs, directions, delta-v, timing, probability, and fuel-proxy
   fields into the Mission Constraint and Decision contracts. The integrated demo evaluates
   15 candidates: 3 positive directions × 5 delta-v values. The original standalone Maneuver
   Agent still supports its six-direction request schema.
8. Connected the Mission Constraint Agent's StateProvider to stored ephemerides. Added
   trajectory-based checks against other moving catalog objects, instead of relying solely
   on the supplied fixed-position secondary-object approximation. Non-improving candidates
   are rejected. A configured zero fuel budget produces explicit escalation.
9. Joined maneuver properties with mission approval records for the Decision Agent.
   Allowed its `ESCALATE_NO_VIABLE_OPTION` result and nullable confidence score.
   Planning uses the highest-Pc recorded encounter per primary; other encounters are retained
   for display and secondary screening, not independently overwritten into a final plan.
10. Connected the Explanation Agent, using its existing mock/provider fallback behavior.
    Added support for phrases with the percentage after the word fuel, such as “fuel to 1%”.
    Report inputs exclude long trajectory arrays.
11. Exposed the supplied Negotiation Agent at `/negotiate`; it is optional and not silently
    represented as having run in a satellite/debris encounter.
12. Added retained background task state, run polling, explicit partial/failure reporting,
    a concurrent-run guard, and cleanup of derived data between runs. Old constraints and
    activity logs persist. A read-only ephemerides endpoint prevents the dashboard from
    overwriting the analysis paths merely to draw them.
13. Replaced the static XY plot with a local perspective 3D view: camera rotation, zoom,
    moving objects, time slider, trajectory preview, magnified relative encounter panel,
    reports, and JSON export. Browser code uses Canvas for 3D projection/depth drawing and
    Hermite interpolation for playback; no Three.js/CDN or build step is required.

## Physical and functional limitations

- Synthetic data are not live observations. The default pipeline refreshes on demand;
  this release is not a continuously scheduled monitoring service.
- SGP4 output is GCRS, labeled `ECI_J2000` by the inherited contract. It is treated as one
  shared inertial frame within this demo. Precision operational frame/time handling is
  outside scope.
- Each object's assumed position covariance is diag(0.01, 0.01, 0.01) km²: 100 m sigma.
  The combined hard-body radius is 15 m. These are demonstration assumptions, not measured
  uncertainty. Covariance is not propagated. Pc should not be presented as calibrated real risk.
- The maneuver model is an approximate two-body differential correction on a nominal SGP4
  trajectory, not a fitted post-burn TLE. The burn time is fixed at 30 minutes before the
  original encounter where lead time permits. The integrated search uses prograde, radial-out,
  and normal burns at 0.1, 0.2, 0.4, 0.8, and 1.5 m/s.
- Candidate screening extends from burn time to the earlier of the requested horizon or
  30 minutes after the original TCA. The result records `screened_until_utc`; it does not
  establish safety over subsequent orbits or against objects absent from the catalog.
- Candidate ranking optimizes the supplied Decision Agent's Pc threshold (default 1e-6)
  and delta-v. A fixed 2 km separation is not a final hard constraint. This is why a computed
  1.2 km candidate can be selected if its assumed-covariance Pc meets the target.
- Fuel percentages are percentages of an assumed remaining 100 m/s delta-v budget, not
  propellant mass estimates. Default budget limit is 5%. Agent 6 altitude and protected-region
  rules remain approximations; no real ground-coverage model is implemented.
- The added secondary screening rejects a candidate if it reduces another object's closest
  separation below 2 km and worsens the original separation. It does not calculate a second
  independent collision probability for every secondary object.
- Decision confidence is a rule-based score, not statistical confidence. The Decision Agent
  may select a partial mitigation when approved candidates miss the target; its reason says so.
- No automatic two-satellite counterfactual planning is implemented. Negotiation operates on
  supplied proposals. No satellite commands or operator messages are transmitted.
- Explanation uses deterministic mock text by default. Optional provider SDKs, credentials,
  model availability, and live CelesTrak access are not verified by the offline tests.
- Module 10 is a local single-process demo: no authentication, durable job queue, multi-user
  isolation, distributed locks, or production deployment is included.

## Verification

The integrated Python suite passes **11 tests** under Python 3.12:

- Analytic centered Gaussian probability and invalid zero relative velocity.
- Refined closest approach occurring between coarse samples.
- All nine registry entries use integration adapters.
- 20-object / 2-hour run with real contributor calls and no failed branches.
- Selected maneuver improves separation and meets the configured probability target.
- Initial position continuity and returned post-maneuver trajectory.
- Explanation report availability with no API keys.
- Zero fuel budget results in no-viable-option escalation.
- Optional negotiation picks the lower-delta-v proposal.
- Health/dashboard/API validation and clean repeated runs.
- “Limit fuel to 1%” operator parsing.

JavaScript syntax was checked with `node --check`. Health, static dashboard serving,
operator command, and negotiation endpoints were also checked directly.
Two upstream deprecation warnings appear from the installed Starlette/httpx testing
dependencies; they did not prevent tests from passing.

Browser verification status is recorded separately in `VERIFICATION.md`.
