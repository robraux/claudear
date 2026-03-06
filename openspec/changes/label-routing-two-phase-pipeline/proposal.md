## Why

Claudear currently maps one Linear team to one repository and runs a single-phase automation (move to Todo, Claude implements, PR created). This prevents using a single Linear team to drive work across multiple repositories and provides no human checkpoint between spec generation and implementation. We need label-based multi-repo routing and a two-phase pipeline (spec, then implement) with a human approval gate between phases to support more complex, multi-repo projects with quality control.

## What Changes

- **BREAKING**: Replace the team-to-repo mapping (`ProviderInstance.repo_path`, `get_instance_repo_path`) with label-based routing. Issues tagged with `repo:<key>` labels resolve to local repo paths via a new `REPO_MAP` configuration. Multiple `repo:` labels on one issue spawn parallel tasks.
- **BREAKING**: Add intake filtering. Only issues assigned to a user in `ALLOWED_ASSIGNEES` AND tagged with `claude:auto` are processed. All others are silently dropped. Remove bot-user dependency for filtering.
- **BREAKING**: Replace the single trigger state ("Todo") with a two-phase state machine. "Ready for Spec" triggers Phase 1 (spec generation via a configurable claude command/prompt). "Ready for Dev" triggers Phase 2 (implementation via a separate configurable command/prompt). Each phase runs in its own worktree and Claude session.
- **BREAKING**: Change the SQLite task store primary key from `issue_id` (or `task_key` = `provider:instance:external_id`) to a composite key of `issue_id + repo_key`, supporting multiple concurrent tasks per Linear issue.
- Add automated state transitions via the Linear API: Phase 1 done moves to "Spec Review", Phase 2 done moves to "In Review", blocked moves to "Blocked". Board status is the single source of truth for pipeline phase - no phase-tracking labels.
- Remove all Notion integration points (provider, poller, client, config).
- Nice-to-have (v2): backward state transitions as recovery (e.g., "Blocked" back to "Ready for Spec" cancels current work and re-triggers the phase fresh).

## Capabilities

### New Capabilities
- `label-repo-routing`: Resolve `repo:<key>` labels on Linear issues to local repository paths. Support multiple repos per issue with parallel task spawning.
- `intake-filter`: Filter incoming webhooks by assignee allowlist and `claude:auto` label presence before processing.
- `two-phase-pipeline`: Two-phase state machine driven by board status. "Ready for Spec" triggers spec generation, "Ready for Dev" triggers implementation. Each phase uses a configurable Claude command/prompt template and runs in an isolated worktree.
- `multi-task-store`: SQLite task storage with composite key `(issue_id, repo_key)` supporting multiple concurrent tasks per Linear issue. Aggregation logic to gate issue-level state transitions on all repo-tasks completing.

### Modified Capabilities
_(none - no existing specs)_

## Impact

- **Config** (`core/config.py`): New env vars - `REPO_MAP` (JSON or comma-separated key=path pairs), `ALLOWED_ASSIGNEES`, phase-specific command templates. Remove Notion config vars. Remove per-team `repo_path` mapping.
- **Task store** (`core/store.py`, `tasks/store.py`): New composite primary key, new columns for `repo_key` and `phase`. Migration needed for existing databases.
- **Orchestrator** (`core/orchestrator.py`): Replace `_start_task` single-phase logic with phase-aware dispatch. Add fan-out for multi-repo tasks and fan-in for completion gating. Add automated Linear state transitions on phase completion.
- **Webhook handler** (`providers/linear/webhook.py`): Add intake filter (assignee + label check) before event dispatch. Extract `repo:` labels and pass to orchestrator.
- **Claude runner** (`claude/runner.py`): No changes to subprocess invocation. Prompt building (`claude/hooks.py`) needs phase-aware templates (spec vs. implementation prompts).
- **Linear provider** (`providers/linear/provider.py`): Add methods for state transitions by name (not just type). Support new board states ("Ready for Spec", "Spec Review", "Ready for Dev", "Blocked").
- **Notion provider** (`providers/notion/`): Entire directory removed.
- **State machine** (`core/state.py`): Add phase-awareness (SPEC_IN_PROGRESS, SPEC_BLOCKED, DEV_IN_PROGRESS, DEV_BLOCKED, etc.) or parametrize existing states with a phase field.
- **Dependencies**: No new external dependencies expected. Linear API already supports all needed operations.
