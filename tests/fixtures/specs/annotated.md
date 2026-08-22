# Annotated — spec
## Goal
Exercise deterministic validation.
## Done looks like
- Every item has checkable metadata.
## Current state
Several bounded changes remain.
## Remaining work
- [ ] Add the parser regression case
      Acceptance: the parser recognizes a wrapped checklist item
      Tests: tests/test_specs.py
      Size: S
      Classes: none
      Verified-missing: the regression case is absent from tests/test_specs.py
- [ ] Document the validator output
      Acceptance: schema documentation lists every output rule
      Tests: inspect docs/SCHEMA.md in the documentation test
      Size: M
      Classes: plan, contract
      Verified-missing: the SPEC appendix is absent
- [ ] Publish a stable response field
      Acceptance: the response includes the new field
      Tests: tests/test_api.py
      Size: S
      Classes: public_api
      Verified-missing: response fixtures do not contain the field
- [ ] Rewrite the whole application
      Acceptance: the replacement passes all existing tests
      Tests: full suite
      Size: L
      Classes: none
      Verified-missing: no replacement exists
- [x] Already completed work
## Non-goals
- Replace the application with an unrelated framework
