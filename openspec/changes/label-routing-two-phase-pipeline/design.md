## Context

Claudear is a Python async application that receives Linear webhooks, creates git worktrees, and runs Claude Code headless (`claude --print`) to implement tickets. The current architecture has:

- **One repo per team**: `ProviderInstance` binds a Linear team to a single `repo_path`. Config resolves via `LINEAR_<TEAM>_REPO` env vars.
- **Single-phase trigger**: A webhook fires when an issue moves to "Todo" (state type `unstarted`). The orchestrator creates one worktree, runs one Claude session, pushes a PR, and moves the issue to "In Review".
- **One task per issue**: The SQLite store uses `issue_id` (legacy) or `provider:instance:external_id` (unified) as the primary key. One issue = one task record.
- **Multi-provider abstraction**: A `PMProvider` / `EventSource` / `TaskOrchestrator` layer supports Linear and Notion. Notion is unused and will be removed.

The Claude runner shells out to the `claude` CLI and must continue doing so (CLI subscription auth). The runner's `_execute_claude` method builds a prompt, pipes it to stdin, and streams JSONL from stdout.

## Goals / Non-Goals

**Goals:**
- One Linear team routes to N repos via `repo:<key>` labels
- Two-phase pipeline (spec, then implement) with human approval gate between phases
- Intake filtering by assignee allowlist + `claude:auto` label
- Multiple concurrent tasks per issue (one per target repo)
- Automated board state transitions as the single phase-tracking mechanism
- Remove Notion provider (dead code)

**Non-Goals:**
- Backward state transitions as recovery (deferred to v2)
- Changing the Claude CLI invocation mechanism
- Supporting non-Linear providers
- Auto-creating `repo:` labels in Linear (operator creates them manually)
- PR merge automation changes (existing flow is fine)

## Decisions

### D1: Repo routing via labels, not config-time team binding

**Decision**: Extract `repo:<key>` labels from the webhook payload and resolve each key against a `REPO_MAP` configuration (env var, JSON dict of `key -> local_path`). Remove the `ProviderInstance.repo_path` field and per-team repo env vars.

**Why**: The current model requires N Linear teams to route to N repos. Labels decouple repo targeting from team structure, letting one team fan out to many repos. Labels are visible on the board, making routing transparent.

**Alternative considered**: Per-issue custom fields. Rejected because Linear custom fields have weaker API support and aren't visible as board-level filters.

**Config shape**:
```
REPO_MAP={"api":"/home/user/repos/api","web":"/home/user/repos/web","infra":"/home/user/repos/infra"}
```

Each key corresponds to a `repo:<key>` label in Linear (e.g., `repo:api`).

### D2: Intake filter in the webhook event source, before event dispatch

**Decision**: Add filtering in `LinearWebhookEventSource._handle_issue_webhook` that checks:
1. Issue has a `claude:auto` label
2. Issue assignee ID is in `ALLOWED_ASSIGNEES` (comma-separated list of Linear user IDs)

If either check fails, drop the event silently (debug log only). This runs before any event is dispatched to the orchestrator.

**Why**: Filtering at the webhook layer is the earliest point with access to issue data and prevents unnecessary event objects from being created. It keeps the orchestrator clean.

**Alternative considered**: Filter in the orchestrator's `_handle_event`. Rejected because it would still create events and task IDs for issues we'll never process.

### D3: Phase tracking via board state names, no phase labels

**Decision**: Map specific Linear workflow state names to pipeline phases:

| Board State | Pipeline Action |
|---|---|
| "Ready for Spec" | Trigger Phase 1 (spec generation) |
| "Spec in Progress" | Set by Claudear when Phase 1 starts |
| "Spec Review" | Set by Claudear when Phase 1 completes (human gate) |
| "Ready for Dev" | Trigger Phase 2 (implementation) - human moves here after approving spec |
| "Dev in Progress" | Set by Claudear when Phase 2 starts |
| "In Review" | Set by Claudear when Phase 2 completes (PR created) |
| "Blocked" | Set by Claudear when Claude is blocked in either phase |
| "Done" | Existing terminal state |

These state names are configurable via env vars (`PHASE1_TRIGGER_STATE`, `PHASE1_ACTIVE_STATE`, `PHASE1_COMPLETE_STATE`, `PHASE2_TRIGGER_STATE`, etc.) with the above as defaults.

**Why**: Board status is already the primary visibility mechanism. Using it as the phase source of truth avoids dual bookkeeping between labels and states. Operators see exactly where each issue is in the pipeline.

**Alternative considered**: Phase-tracking labels (`phase:spec`, `phase:dev`). Rejected per requirements - board status should be the single source of truth.

### D4: Composite task key = `issue_id + repo_key`

**Decision**: Change the task store primary key to `(issue_id, repo_key)` where `repo_key` is the key from `REPO_MAP` (e.g., "api", "web"). Add a `phase` column (`spec` or `dev`) to track which phase a task record represents.

The `task_key` format becomes: `linear:<team>:<issue_uuid>:<repo_key>`.

**Why**: One issue with 3 `repo:` labels needs 3 independent tasks, each with its own worktree, branch, and Claude session. The current single-key model can't represent this.

**Completion gating**: When a task completes, query all tasks for the same `issue_id` + `phase`. Only transition the Linear issue state when all repo-tasks for that phase are done. If any task is blocked, transition the issue to "Blocked".

### D5: Phase execution via Claude Code custom commands

**Decision**: Each phase is a Claude Code custom command stored as a markdown file in the target repo's `.claude/commands/` directory:

- **Phase 1**: `.claude/commands/generate-spec.md` - reads the Linear ticket via MCP, generates OpenSpec artifacts (`/opsx:ff` or `/opsx:explore` + `/opsx:continue`), posts a summary comment, commits and pushes.
- **Phase 2**: `.claude/commands/implement-spec.md` - reads the approved spec from `openspec/changes/`, applies it via `/opsx:apply`, runs tests, verifies via `/opsx:verify`, archives via `/opsx:archive`, creates a draft PR, posts the PR link.

The `ClaudeRunner` invocation changes slightly: instead of piping a prompt to stdin, it invokes `claude --print -p "/<command-name> <ticket-identifier>"` to trigger the custom command. The runner still shells out to the `claude` CLI with `--print --dangerously-skip-permissions --output-format stream-json`.

The orchestrator selects the command name based on which trigger state fired:
- "Ready for Spec" fires `PHASE1_COMMAND` (default: `generate-spec`)
- "Ready for Dev" fires `PHASE2_COMMAND` (default: `implement-spec`)

Command names are configurable via env vars. The command files themselves live in each target repo (not in Claudear) so they can be customized per-repo.

**Why**: Custom commands are the idiomatic Claude Code way to define reusable workflows. They can reference the repo's CLAUDE.md, use MCP tools, invoke OpenSpec skills, and be version-controlled alongside the repo. This is far cleaner than multi-line env vars or prompt template files managed by Claudear.

**Alternative considered**: Prompt templates stored as files in Claudear (e.g., `prompts/spec.md`). Rejected because custom commands are repo-local, can use slash-command syntax, and benefit from Claude Code's built-in command resolution. Claudear shouldn't own prompt content that's specific to each target repo.

### D6: Fan-out / fan-in in the orchestrator

**Decision**: When a trigger state is detected:

1. **Fan-out**: Extract `repo:<key>` labels from the issue. For each key, create an independent task (worktree, branch, Claude session). Tasks run concurrently up to `max_concurrent_tasks`.

2. **Fan-in**: Each task completion checks if all sibling tasks (same issue_id + phase) are complete. Only then does the orchestrator transition the Linear issue to the next state.

The `_start_task` method becomes `_start_phase(task_id, phase, repo_keys)` which spawns N tasks. Each task's completion callback calls `_check_phase_complete(issue_id, phase)`.

**Why**: This is the minimal coordination needed. Tasks are independent (different repos, different worktrees), so fan-out is trivially parallel. Fan-in is a simple "all done?" check.

### D7: Remove Notion provider entirely

**Decision**: Delete `claudear/providers/notion/`, remove Notion config from `MultiProviderSettings`, remove Notion references from `core/app.py` and `unified_main.py`.

**Why**: Notion integration is unused and adds maintenance burden. The multi-provider abstraction (`PMProvider`, `EventSource`) stays - it's useful for potential future providers - but the Notion implementation goes.

## Risks / Trade-offs

- **Board state name fragility**: If operators rename workflow states in Linear, Claudear breaks. Mitigation: configurable state names via env vars, with clear error messages when expected states aren't found.

- **Fan-in race condition**: Two repo-tasks completing simultaneously could both check "all done?" and both try to transition the issue. Mitigation: use an async lock per issue_id, and make the Linear state transition idempotent (setting the same state twice is harmless).

- **Webhook payload may lack labels**: Linear webhook `Issue.update` payloads include label changes but the issue data may not always embed the full label list. Mitigation: on trigger-state webhooks, fetch the full issue (with labels) from the API before fan-out.

- **Worktree cleanup on multi-repo failures**: If 2 of 3 repo-tasks succeed but 1 fails, we have partial state. Mitigation: move issue to "Blocked", post a comment listing which repos succeeded and which failed. Operator resolves manually.

- **Migration for existing SQLite data**: The primary key change is destructive. Mitigation: since this is a pre-production tool, a simple "drop and recreate" migration is acceptable. Add a schema version table for future migrations.

## Resolved Questions

- **Phase templates**: Stored as Claude Code custom commands (`.claude/commands/*.md`) in each target repo, not as env vars or Claudear-managed files. See D5.
- **Per-repo default branches**: `main` is always the base. `REPO_MAP` is a simple `key -> path` mapping.
- **Phase invocation**: Custom commands triggered via `claude --print -p "/<command-name> <ticket-id>"`. Phase 1 runs `generate-spec`, Phase 2 runs `implement-spec`. See D5.
