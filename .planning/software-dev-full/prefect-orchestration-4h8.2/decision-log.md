# Decision log: prefect-orchestration-4h8.2

- **Decision**: Derive per-role `last-iter` / `last-updated` from artifact
  files rather than adding new metadata.json keys.
  **Why**: `metadata.json` on existing runs only carries `session_<role>`
  UUIDs — no per-role iter/timestamp. Adding new keys would require a
  coordinated change in the software-dev pack (`po_formulas/*`), expanding
  the blast radius of a read-only CLI verb. Plan §Approach makes the
  artifact-scan approach explicit.
  **Alternatives considered**: (a) extend pack to write `iter_<role>` and
  `updated_<role>`; (b) just leave iter/updated blank.

- **Decision**: `MetadataNotFound` → exit code 3; unknown role on
  `--resume` → exit code 4; `RunDirNotFound` → exit code 2.
  **Why**: Mirrors `po logs` exit-code ladder (2 for resolver failure,
  3 for missing file) so users/scripts can branch consistently. Reserves
  4 for role-lookup misses so they're distinguishable from metadata
  corruption.
  **Alternatives considered**: collapsing all errors to exit 1.

- **Decision**: Emit the `--resume` one-liner to stdout with no
  surrounding text.
  **Why**: Pipe-friendly (`po sessions <id> --resume builder | xargs -r bash -c`)
  and the AC wording ("ready-to-run one-liner") implies copy-paste purity.
  **Alternatives considered**: wrap in a `$ ` prefix or a trailing `# role`
  comment; both break `xargs`/`eval`.

- **Decision**: Surface every `session_*` key — including unknown roles —
  with `-` placeholders for iter/updated.
  **Why**: Plan's risk section calls out metadata schema drift; future
  roles added by the pack should appear immediately without a core change.
  **Alternatives considered**: hard-fail on unknown roles.
