# Build Loop — The Loom

Autonomous TDD build loop. You are the builder. Follow this process exactly.

## Target

$ARGUMENTS

If a task is specified (e.g., "Task 0.2", "Phase 1"), work on that.
If empty, read BUILD_PLAN.md and pick the next incomplete task (first unchecked `- [ ]`).

---

## Phase 1: Orient (do this FIRST, every time)

1. Read `BUILD_PLAN.md` — locate the target task and its acceptance criteria
2. Skim `CLAUDE.md` — refresh conventions relevant to this task
3. Check state:
   - `git status` — uncommitted work? Stash or commit first.
   - `docker compose ps` — services running? Start if needed: `docker compose up -d`
   - Wait for postgres/redis healthy before proceeding
4. Use **TaskCreate** to break the target task into testable units. Each unit = one RED→GREEN→REFACTOR cycle. Order them by dependency (models before API before frontend).

## Phase 2: The Loom (repeat for each unit)

### RED — Failing Test First

- Write ONE test for the smallest provable behavior
- Test file location: `apps/<app>/tests/test_<thing>.py`
- Use `baker.make(Model)` for instances, not manual construction
- Run: `docker compose exec backend pytest <test_file>::<TestClass>::<test_name> -x -v`
- **MUST fail.** If it passes → the feature exists or the test is vacuous. Fix the test.

### GREEN — Minimal Pass

- Write the **minimum** code that makes the failing test pass
- No "while I'm here" additions. No speculative abstractions.
- Run the same pytest command
- **MUST pass.**

### REFACTOR — Clean While Green

- Improve naming, remove duplication, apply CLAUDE.md conventions
- Run lint: `docker compose exec backend ruff check --fix . && docker compose exec backend ruff format .`
- Run full suite: `docker compose exec backend pytest`
- **ALL tests MUST pass.** If anything broke, fix it before moving on.

### Mark the unit done in your task list, then start the next unit.

---

## Phase 3: Verify (after all units complete)

Run these checks — all must pass before the task is done:

```bash
docker compose exec backend pytest -v                              # full test suite
docker compose exec backend python manage.py migrate --check       # no unapplied migrations
docker compose exec backend ruff check .                           # lint clean
```

If the task touched frontend:
```bash
docker compose exec frontend pnpm check                            # svelte/ts check
```

Then verify BUILD_PLAN.md acceptance criteria (e.g., "curl localhost:8000/health/ returns ok").

## Phase 4: Improve (meta — after each task)

Reflect on the cycle you just completed. Only act on things that help FUTURE builds:

1. **Permission friction** — Were there repeated permission prompts for safe commands?
   → Add the pattern to `.claude/settings.json` `permissions.allow`

2. **Convention gaps** — Did you make a judgment call not covered by CLAUDE.md?
   → Propose the addition to the user (don't silently change conventions)

3. **Skill gaps** — Was a step in this build process missing or unhelpful?
   → Edit this file (`.claude/commands/build.md`) with the improvement

4. **Learnings** — Anything non-obvious about the codebase, a gotcha, a pattern?
   → Save to memory (only if it will matter in future conversations)

5. **Hook opportunities** — A check you ran manually every time?
   → Add it to `.claude/settings.json` hooks

---

## Decision Gates — When to Ask the User

**ASK** (these shape the product):
- Data model design — field names, types, relationships, constraints
- API contract — endpoint URLs, response shapes, status codes
- Ambiguous requirements in BUILD_PLAN.md
- Trade-offs between two valid approaches (present both with trade-offs)
- Anything that crosses phase boundaries or changes public interfaces
- UI/UX choices that affect user experience

Format: "I need a decision on X. Option A: [desc] (trade-off: ...). Option B: [desc] (trade-off: ...). Recommendation: [your pick and why]."

**DON'T ASK** (just follow conventions):
- File placement, test structure, import order
- Implementation details within a clear task
- Code style (ruff + CLAUDE.md rules)
- Migration contents (follow from models)
- Which library to use (stack is locked in CLAUDE.md)

---

## Agent Strategy

Use agents to parallelize work and protect context:

- **Explore agent** — Before starting, if you need to understand existing code across multiple files
- **Plan agent** — If a task has 5+ units, plan the breakdown before coding
- **Background agents** — Run full test suite in background while writing the next test
- **Parallel agents** (worktree isolation) — When BUILD_PLAN.md marks tasks as PARALLEL, run them in separate worktrees simultaneously

---

## Continuous Mode

This skill handles ONE task per invocation. For continuous building across a phase:
- User runs: `/loop /project:build`
- Each invocation picks the next incomplete task
- Stop at phase boundaries — the user should review before crossing into the next phase
