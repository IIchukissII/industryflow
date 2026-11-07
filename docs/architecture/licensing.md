<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Licensing

The licence each file carries and where the licence texts live. Manual of
[ADR-0001](../../ADR/ADR-0001-industryflow-framing.md).

| Content | Licence | Header |
|---|---|---|
| Code, configuration, scripts | AGPL-3.0-or-later | `SPDX-License-Identifier: AGPL-3.0-or-later` |
| Documentation, ADRs | CC-BY-SA-4.0 | `SPDX-License-Identifier: CC-BY-SA-4.0` |
| A file that cannot carry a header | As declared in `REUSE.toml` | — |

## ADR-0001 dec 2

Code files carry an AGPL-3.0-or-later SPDX header, documentation a CC-BY-SA-4.0 one; files that cannot carry a header are declared in the manifest.

| Code | Role |
|---|---|
| `LICENSE` | AGPL-3.0-or-later text at the repository root |
| `LICENSES` | Every licence text, one file per SPDX identifier |
| `REUSE.toml` | Licence of the files that carry no header |

## ADR-0001 dec 2

Code files carry an AGPL-3.0-or-later SPDX header, documentation a CC-BY-SA-4.0 one; files that cannot carry a header are declared in the manifest.

| Code | Role |
|---|---|
| `LICENSE` | AGPL-3.0-or-later text at the repository root |
| `LICENSES` | Every licence text, one file per SPDX identifier |
| `REUSE.toml` | Licence of the files that carry no header |
