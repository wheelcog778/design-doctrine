# Design Doctrine for Decision Tools · v2.20.0

> Build the thing that can kill your idea — before you build your idea.

A process card and a set of runnable checkers for one job: building a tool that someone will
use to **make a decision** (buy / select / rank / flag / approve), and auditing one that already exists.

Not a tutorial. It is a checklist you execute, plus four zero-dependency checkers — three of
which exit non-zero on purpose.

- Pure Python standard library (3.9+), zero dependencies
- Ships as an agent skill (`SKILL.md`) or as a plain A4 process card
- Every gate is a number, not an adjective

---

## The one claim

> **A conclusion you cannot recompute is not a conclusion.**

Everything else in this package is machinery for that sentence: write the failure criterion before
the first line of code, one metric gets one algorithm, acceptance is defined twice (once by machine,
once by a human who has watched someone use it), and the last section of any report is
**"what this did not cover."**

## What is inside

```
SKILL.md                     the entry point: 5-second router, triage, hard gates, M1–M5
README.md                    this file
scripts/
  doctrine_check.py          pre-delivery check: required sections, banned words, --gates
  doc_dup_scan.py            cross-file duplicate lines; how many places one fact lives in
  version_lint.py            single source of versions (SKILL frontmatter vs README)
  check_freshness.py         before you start: version / newest artifact / git HEAD
references/
  workflow.md                two entry points, six stages, two acceptance rounds, tiers
  veto-gates.md              the seven gates that decide "continue or kill"
  ten-laws.md                ten rules in three layers, and which ones have no evidence behind them
  micro-action-audit.md      counting inference steps (M1) instead of guessing
  templates.md               pre-registration card, acceptance lists, delivery status card
  repair-loop.md             the seven-step repair loop
  consolidation-pass.md      collapsing synonyms before they become two sources of truth
  gate-and-event-study.md    mean vs tail criteria, cluster-robust t, promotion rules
  efficacy-study.md          proving a pipeline is actually worth running
  self-check-tools.md        writing checkers that check the package itself
  handover-review.md         reviewing someone else's delivery
  public-release.md          how to sanitize and generalize this package for others
  intake.md                  three questions before you accept any task
examples/
  synthetic-walkthrough.md   one full pass on invented data (deliberately not citable)
```

## Quick start

```bash
python scripts/doctrine_check.py examples/synthetic-walkthrough.md   # must be judged NOT citable
python scripts/doctrine_check.py <your-report>.md --explain
python scripts/doctrine_check.py <your-status-card>.md --gates
python scripts/doc_dup_scan.py . --metric M1
python scripts/version_lint.py
```

An agent can use the directory as-is: drop it under a skills directory and the router in `SKILL.md`
decides what to read for a given task. A human can use `SKILL.md` alone and open `references/` only
when the router points there.

## What this does not do

- It does not tell you whether your conclusion is **true**. It catches the cheap ways a conclusion
  becomes untrustworthy: no failure criterion, two algorithms for one number, a report that never
  says what it missed.
- It does not supply thresholds. Every gate in `veto-gates.md` says "calibrate this in your domain"
  and then hands you the calibration procedure (`workflow.md` §9). A package that ships universal
  cut-offs is teaching you to copy someone else's noise floor.
- It is not validated outside its original domain.

**Calibration domain, stated plainly:** A-share quantitative stock selection and backtesting.
The L0–L5 tiers and the M1 line were calibrated there. The domain vocabulary that survives in the
examples — candidates, position sizing, transaction cost — is deliberate, not residue. If you work
in another domain, keep the gate structure and re-derive every threshold from scratch.

## Evidence level: assumption-grade (L0–L1)

This public version is **derived from an internal package**. Everything that made the internal
version stronger than an opinion was removed from it: the measured numbers, the project identities,
the case studies, the run logs.

What was deleted, and what it cost:

| Deleted | Why it had to go | Cost |
|---|---|---|
| Case studies with real measurements | sample sizes and interval arithmetic are the most precise fingerprint there is | cannot show "this rule killed a project worth real money" |
| All measured effects and t-values | a reader cannot recompute them from this package | the ten rules lose their evidence column and become hypotheses |
| Internal paths, project names, data sources | they identify the author and the domain | examples had to be rebuilt synthetically |

**Therefore: every rule here is a hypothesis, not a finding.** Cite it as experience, never as a
result. Do not copy thresholds anywhere — there are none. If you want the numbers, the honest answer
is that they are not in this repository, and that is deliberate.

This is the package applying its own rule: *do not lend credibility with evidence the reader cannot see.*

## Relationship to the internal version

This repository is a derived distribution. It does **not** evolve on its own; it is re-cut from the
internal package at the same version number. The internal version is the single source of truth for
the method. Two copies coexisting is not drift — but only if the relationship is written down, which
is what this section is for.

## Changelog

**v2.20.0** — first public cut. Sanitized: case studies, measured results and internal identifiers
removed; three reference files extracted to replace them (`ten-laws.md`, `veto-gates.md`,
`intake.md`); one synthetic walkthrough added so the package can be exercised end to end.

## License

CC BY 4.0 — see `LICENSE`. Attribution: wheelcog778.