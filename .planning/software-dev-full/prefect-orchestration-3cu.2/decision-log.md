# Decision Log: prefect-orchestration-3cu.2

## Build iter 1

- **Decision**: Use the SDK (`google-api-python-client`) as the primary
  client; do **not** add a parallel `gcloud`/`gcal` CLI codepath.
  **Why**: triage flagged that `gcloud` has no native Calendar verbs;
  CLI tools like `gcal`/`khal` differ in semantics, are
  inconsistently installed, and would double the surface for tests.
  **Alternatives considered**: shelling out to `gcal`(1) when
  available — rejected because the user-facing surface (the `po gcal-*`
  commands) is itself the agent's CLI; the "CLI-first" framing in the
  skill is preserved by pointing agents at `po`, not by avoiding
  Python under the hood.

- **Decision**: `gcal-create` reads a Google Calendar `Event`-resource
  JSON object on stdin (no flags for nested fields).
  **Why**: triage left input format unspecified. The Event resource has
  nested objects (`start`, `end`, `attendees`, `recurrence`) that don't
  map to flat `--key=value` flags cleanly; stdin JSON is the canonical
  shape for SDK callers and matches the agent's mental model.
  **Alternatives considered**: ICS, key=value lists, separate flags
  per common field — all pile up complexity without solving recurrence
  / multiple attendees.

- **Decision**: `gcal-free` requires explicit `--start`/`--end` (no
  default window).
  **Why**: triage flagged the parameter ambiguity ("point-in-time vs
  range"). Requiring both removes the ambiguity and keeps the agent
  honest about what window it's asking about. Predictability > brevity.
  **Alternatives considered**: defaulting to "today", `start + 1h`,
  or accepting only a duration — all baked policy into the command
  that callers may not want.

- **Decision**: Auth is service-account JSON (`GOOGLE_APPLICATION_CREDENTIALS`)
  + ADC only. **No interactive OAuth dance**.
  **Why**: agents run headless; an interactive flow would hang. The
  auth-troubleshooting hint in the skill + `po doctor` checks tell the
  user how to set it up once per host (`gcloud auth application-default
  login`).
  **Alternatives considered**: shipping our own OAuth installed-app
  flow that opens a browser — rejected for the headless reason.

- **Decision**: Pack lives at `/home/ryan-24/Desktop/Code/personal/nanocorps/po-gcal/`,
  a fresh sibling next to `prefect-orchestration/` and `software-dev/`.
  **Why**: the issue is pack authoring; nothing in core needs to
  change. Triage explicitly called this out (don't land code in
  `prefect-orchestration/`). Mirrors the layout of
  `../software-dev/po-formulas/`.
  **Alternatives considered**: dropping it under `software-dev/` —
  rejected because that pack is named for a *use case* (software
  development pipeline) and adding gcal would muddy its identity.

- **Decision**: Doctor-check `calendar_reachable` short-circuits
  yellow when creds are missing, instead of returning red.
  **Why**: a green "creds present" + red "reachable" is informative;
  a red on both rows is double-counting the same root cause and
  clutters the table on a fresh machine. Yellow with "skipped: no
  creds" delegates the action to the creds-row hint.
  **Alternatives considered**: returning red — rejected for double
  counting; skipping the row entirely — rejected because users want
  to see the check exists.

- **Decision**: Mock the Google SDK at the `po_gcal._client.build_service`
  seam in tests; never import `googleapiclient` from the test suite.
  **Why**: keeps the test suite hermetic and fast, follows "testing-patterns.md"
  norm of not making live API calls in unit tests, and the seam is the
  natural injection point.
  **Alternatives considered**: mocking `googleapiclient.discovery.build`
  directly — works but couples tests to an SDK internal name that
  could change; mocking at our own seam is more durable.
