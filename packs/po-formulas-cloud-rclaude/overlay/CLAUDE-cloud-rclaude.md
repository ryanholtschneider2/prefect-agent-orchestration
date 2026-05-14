# cloud-rclaude

**What it provides:** `po env` driver for DigitalOcean (and Hetzner) VMs via
the rclaude provisioning stack. Registers `--driver rclaude`.

**When to use:**
- Running `software-dev-fast` / `software-dev-full` on a remote VM
- Need OAuth-authenticated Claude Code on a fresh droplet without an API key

**Key verbs:** `po env up --driver rclaude --backend digitalocean`,
`po run <formula> --env <name>`, `po attach <name>`, `po env down <name>`

**Key paths:** `packs/po-formulas-cloud-rclaude/po_formulas_cloud_rclaude/driver.py`

**Skip if:** running locally or using Daytona/Modal.

**Read more:** `po show env-up`, `engdocs/cloud-envs.md`
