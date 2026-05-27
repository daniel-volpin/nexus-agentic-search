<!--
PR template for spec-driven PRs. Replace placeholders, keep checkboxes.
Delete sections that don't apply (e.g. "Adversarial tests" if not a security change).
-->

## Spec & plan

- Spec: `docs/specs/NN-name.md`
- Plan: `docs/plans/NN-name-plan.md`

## Summary

<!-- 1–3 bullets. What does this PR deliver from the plan's build order? -->

-

## Spec invariants covered

<!-- Pick the invariants from the spec's "Invariants" section that this PR
makes hold. Each must reference a test that asserts it. -->

- [ ] `<invariant 1>` — verified by `tests/.../test_x.py::test_y`
- [ ] `<invariant 2>` — verified by `tests/.../test_x.py::test_z`

## Adversarial tests (Spec 13)

<!-- Only required if this PR touches a security-sensitive surface (crawl, llm
keys, transport auth, citations). Remove this section otherwise. -->

- [ ] SSRF / envelope / redaction / citation / auth catalog applicable to this
      surface lives under `tests/security/` and passes.

## Test plan checklist

- [ ] `make lint` clean
- [ ] `make test` green
- [ ] `make typecheck` reviewed (advisory; new code typed where reasonable)
- [ ] No spec invariant left without a test
- [ ] No `# TODO`/`# FIXME` defers a security control

## Done criteria (from plan)

- [ ]

## Notes for reviewer

<!-- Anything weird? Tradeoffs you took? Things you punted to a follow-up? -->
