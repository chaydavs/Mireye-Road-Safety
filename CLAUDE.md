# CLAUDE.md — Subgrade project constitution

These are standing rules for every session in this repo. They override default behavior.

## Authority
- This project implements **PRD-subgrade.md**. When a decision is ambiguous, the PRD wins.
- If the PRD is silent on something, **ask** — do not invent scope.

## Lean code only
No placeholder functions, no "future use" parameters, no abstractions with a single caller,
no classes where a function does the job, no config options nobody sets. If a line is not needed
for the current session's definition of done, do not write it.

## Field-name validation (CRITICAL)
Every Mireye field name used anywhere in code must be validated at runtime against the live
catalog at `https://api.mireye.com/v1/meta/fields` **before fetching**. Never trust a field name
from memory — including your own.

## Null handling
Null handling is driven by each field's `null_meaning` from the catalog. A null is **never**
silently treated as zero or false.

## Provenance (CRITICAL)
Every fetched value is stored with its provenance: `value, source, source_url, fetched_at,
confidence`. **No provenance row, no value.**

## Definition of done
For any task, all three must hold before reporting done:
1. `ruff check` passes with zero warnings,
2. `pytest` passes,
3. the session's stated end-to-end check runs successfully.
Do not report done before all three.

## Error log
When you make an error and catch it (or the user catches it), append **one line** to
`ERRORS.md`: what was wrong, how it was caught. Do not editorialize.
