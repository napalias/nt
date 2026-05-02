# TDD Cycle — Single Unit

Execute one focused RED → GREEN → REFACTOR cycle.

## Target

$ARGUMENTS

If no target specified, ask the user what behavior to test.

---

## RED — Write One Failing Test

1. Identify the smallest testable behavior from the target
2. Pick the right app: `apps/<app>/tests/test_<thing>.py`
3. Use `baker.make(Model)` for model instances
4. Write ONE test — clear name: `test_<thing>_<behavior>`
5. Run:
   ```bash
   docker compose exec backend pytest <test_file>::<test_name> -x -v
   ```
6. **Confirm: test FAILS** with a clear, expected error (not an import error or typo)

If the test passes → the behavior already exists or the test is vacuous. Investigate before proceeding.

## GREEN — Minimal Code to Pass

1. Write the **minimum** implementation that makes the test pass
2. No extras: no validation for cases the test doesn't cover, no helper functions you don't need yet
3. Run:
   ```bash
   docker compose exec backend pytest <test_file>::<test_name> -x -v
   ```
4. **Confirm: test PASSES**

## REFACTOR — Improve While Green

1. Clean up: better names, remove duplication, extract only if there are 3+ copies
2. Apply CLAUDE.md conventions:
   - Type hints on all functions
   - `verbose_name` on model fields
   - `geography=True, srid=4326` for point fields
   - No hardcoded radius (use `settings.DEFAULT_SEARCH_RADIUS_M`)
3. Run:
   ```bash
   docker compose exec backend ruff check --fix . && docker compose exec backend ruff format .
   docker compose exec backend pytest
   ```
4. **Confirm: ALL tests still pass**

---

## Output

After the cycle, report:
- What was tested (the behavior, not the method name)
- What was implemented
- What was refactored
- Natural follow-up tests (don't implement them — just note them)
