<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Decision records

How decision records, their manuals and the code they govern are kept apart and kept consistent.
Manual of [ADR-0000](../../ADR/ADR-0000-decision-records-and-source-of-truth.md).

## ADR-0000 dec 2

| Level | Lives in | Holds | Links to |
|---|---|---|---|
| Meaning | `ADR/` | The decision, its reasons, rejected alternatives, costs | Its manual, related ADRs, the glossary, the canon |
| Manual | `docs/`, component READMEs | How each decision works; the code that realises it | The decision it follows |
| Form | The code, configuration and infrastructure | The behaviour and the values | — |

A manual section is headed by the decision it follows and ends with a code table.

| Element | Form |
|---|---|
| Section heading | `## ADR-NNNN dec N`, one or more decisions of one ADR (`## ADR-0033 dec 4, 5`) |
| Body | How the decision works, in tables, without its reasons |
| Code table | Header `\| Code \| Role \|`; each row names a repository path in backticks, or `path::symbol` for a name inside a file |
| ADR References | The manual's link; never a path, symbol, endpoint, variable, port, table or command |

| Code | Role |
|---|---|
| `tests/config_guards/test_manual_code_names.py::test_every_code_name_exists` | Every path and symbol in a manual's code table exists |
| `tests/config_guards/test_manual_code_names.py::test_no_adr_names_code` | No ADR names a repository path |

## ADR-0000 dec 3

Each unavoidable copy of a fact has a guard that reads every copy and fails when they disagree.

| Code | Role |
|---|---|
| `tests/config_guards` | One test per list of copies; each reads both sides |
| `tests/config_guards/_repo.py` | Shared readers of workflows, compose file and chart; holds no list of its own |
| `.github/workflows/config-guards.yml` | Runs the guards on every change ([CI](../operations/ci.md)) |

## ADR-0000 dec 10

| Check | Fails when |
|---|---|
| Cited ADR | `ADR-NNNN` names a record that does not exist |
| Cited decision | `ADR-NNNN dec N` names a number the record's Decision section does not hold |
| Relative link | A relative Markdown link names a file that does not exist |

| Code | Role |
|---|---|
| `tests/config_guards/test_adr_references.py::test_every_cited_adr_exists` | Cited ADR |
| `tests/config_guards/test_adr_references.py::test_every_cited_decision_exists` | Cited decision |
| `tests/config_guards/test_adr_references.py::test_every_relative_markdown_link_resolves` | Relative link |
| `tests/config_guards/test_adr_references.py::test_the_guard_is_looking` | The guard parses decisions and finds citations |
