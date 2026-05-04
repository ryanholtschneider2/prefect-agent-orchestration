# po-formulas-retro

Small PO formula pack for scheduled reflection runs over prior `.planning/`
artifacts.

## What it does

- discovers recent run directories under `<rig>/.planning/*/*/`
- extracts repeated friction signals from verdict JSON plus
  `decision-log.md` / `lessons-learned.md`
- writes a durable report under
  `<rig>/.planning/update-prompts-from-lessons/<report-slug>/`
- optionally files deduped follow-up beads for worthwhile improvements

The first version writes durable on-disk reports and does not extend
`po artifacts`.

## Install

```bash
po install --editable /path/to/prefect-orchestration/packs/po-formulas-retro
po update
po packs
```

## Manual use

```bash
po run update-prompts-from-lessons \
  --rig-path /path/to/rig \
  --lookback-days 7
```

To allow bead creation:

```bash
po run update-prompts-from-lessons \
  --rig-path /path/to/rig \
  --lookback-days 7 \
  --auto-file-beads
```

## Deployments

- `update-prompts-from-lessons-manual`
- `update-prompts-from-lessons-weekly`

The weekly deployment runs on Monday morning in `America/New_York`.
