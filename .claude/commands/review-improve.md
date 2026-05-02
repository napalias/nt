# Review & Improve

Review recent work and improve the build process itself. Run this after completing a task or a series of TDD cycles.

## 1. Code Review

Check `git diff HEAD~3..HEAD` (or appropriate range) for all recent changes.

For each changed Python file, verify:
- [ ] Type hints on all function signatures
- [ ] `verbose_name` on all model fields
- [ ] Geography fields use `geography=True, srid=4326`
- [ ] No hardcoded distances — uses `settings.DEFAULT_SEARCH_RADIUS_M`
- [ ] No raw SQL where the ORM works
- [ ] No direct DB writes in Scrapy spiders (pipelines only)
- [ ] Tasks decorated with `@shared_task`

For each changed Svelte/TS file, verify:
- [ ] Svelte 5 runes (`$state`, `$derived`, `$effect`) — not legacy `$:`
- [ ] API types from generated `schema.d.ts`, not hand-written
- [ ] Filter state from URL (`$page.url`), not component state

Run the full quality gate:
```bash
docker compose exec backend ruff check .
docker compose exec backend pytest -v
docker compose exec backend python manage.py migrate --check
```

Fix anything that fails before proceeding to process review.

## 2. Process Review

Evaluate whether the build process itself can be improved:

### Permissions
Review the last session's tool calls. Were there permission prompts for commands that are always safe?
→ Add patterns to `.claude/settings.json` → `permissions.allow`

### Hooks
Did you manually run the same check more than twice?
→ Automate it as a hook in `.claude/settings.json`

### Skills
Was a step in `/project:build` confusing, missing, or unnecessary?
→ Edit `.claude/commands/build.md` directly

### Conventions
Did a new pattern emerge that future sessions should follow?
→ **Propose** the CLAUDE.md change to the user — don't change conventions silently

### Memory
Was there a non-obvious learning about the codebase, a gov data source, or a gotcha?
→ Save to memory with context about why it matters

## 3. Report

Summarize in ≤10 lines:
- Issues found and fixed (with file:line references)
- Process improvements made (which files changed)
- Convention proposals for user approval
- Remaining concerns or tech debt noted
