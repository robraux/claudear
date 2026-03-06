## 1. Remove Notion Provider

- [ ] 1.1 Delete `claudear/providers/notion/` directory (client.py, poller.py, provider.py, __init__.py)
- [ ] 1.2 Remove Notion config from `MultiProviderSettings` in `core/config.py` (notion_api_key, notion_database_ids, notion_poll_interval, get_notion_database_ids, get_notion_instance, has_notion, and related validation)
- [ ] 1.3 Remove Notion references from `core/app.py`, `unified_main.py`, and any startup/registration code
- [ ] 1.4 Remove Notion from `ProviderType` enum if it won't break existing DB records (or leave as deprecated)
- [ ] 1.5 Verify the application starts cleanly without Notion configuration

## 2. Repo Map Configuration

- [ ] 2.1 Add `REPO_MAP` env var to `MultiProviderSettings` as a JSON string field. Parse it into a `dict[str, Path]` with a property method
- [ ] 2.2 Add startup validation: REPO_MAP is present, valid JSON, each path exists and is a git repo. Log warnings for invalid paths, error if no valid entries
- [ ] 2.3 Remove `ProviderInstance.repo_path`, `get_instance_repo_path`, and per-team `LINEAR_<TEAM>_REPO` env var resolution from config
- [ ] 2.4 Update `InstanceResources` and `register_instance` in the orchestrator to work without per-instance repo paths (worktree managers will be created per-task, not per-instance)
- [ ] 2.5 Add tests for REPO_MAP parsing: valid JSON, invalid JSON, missing paths, empty map

## 3. Intake Filter

- [ ] 3.1 Add `ALLOWED_ASSIGNEES` env var to `MultiProviderSettings` as a comma-separated string. Add startup validation (required, non-empty)
- [ ] 3.2 Add intake filter logic to `LinearWebhookEventSource._handle_issue_webhook`: fetch full issue from Linear API, check assignee ID against allowlist, check for `claude:auto` label
- [ ] 3.3 Ensure filter only applies to state-change events, not comment webhooks for tracked tasks
- [ ] 3.4 Add DEBUG-level logging for filtered-out issues (reason: assignee, label, or both)
- [ ] 3.5 Add tests for intake filter: allowed assignee + auto label passes, missing label drops, wrong assignee drops, unassigned drops, comment on tracked task bypasses filter

## 4. Label-Based Repo Resolution

- [ ] 4.1 Add a `resolve_repo_labels(issue_id: str) -> list[str]` method that fetches the full issue from Linear API and extracts `repo:<key>` labels, returning resolved keys
- [ ] 4.2 Add validation: warn and skip unknown keys, warn and skip if no repo labels found
- [ ] 4.3 Integrate repo resolution into the webhook handler: on trigger-state events, resolve repos before dispatching to orchestrator. Pass `repo_keys` list alongside the event
- [ ] 4.4 Add tests for label extraction: single repo label, multiple repo labels, unknown key skipped, no repo labels

## 5. Multi-Task Store

- [ ] 5.1 Update the `tasks` table schema: change primary key to composite `(issue_id, repo_key)`, add `repo_key TEXT NOT NULL` and `phase TEXT NOT NULL` columns, update `task_key` format to `linear:<team>:<issue_uuid>:<repo_key>`
- [ ] 5.2 Add schema migration logic: detect old schema on startup, drop and recreate table, log warning about data loss. Add a `schema_version` table
- [ ] 5.3 Update `TaskRecord` dataclass: add `repo_key` and `phase` fields
- [ ] 5.4 Update `TaskStore.save()`, `get()`, `get_by_key()` to use the new composite key
- [ ] 5.5 Add `get_by_issue_and_phase(issue_id: str, phase: str) -> list[TaskRecord]` query method for fan-in
- [ ] 5.6 Add `check_phase_complete(issue_id: str, phase: str) -> tuple[bool, Optional[str]]` that returns `(all_complete, blocked_reason)` - true if all tasks for that issue+phase are completed, or the reason if any are blocked
- [ ] 5.7 Update the unified `core/store.py` (TaskStore) to match - same composite key, repo_key, phase columns
- [ ] 5.8 Add tests for multi-task store: create two tasks for same issue, query by issue+phase, fan-in check with all complete, fan-in check with one blocked, fan-in check with one in-progress

## 6. Two-Phase Pipeline State Machine

- [ ] 6.1 Add phase-related board state configuration to `MultiProviderSettings`: `PHASE1_TRIGGER_STATE`, `PHASE1_ACTIVE_STATE`, `PHASE1_COMPLETE_STATE`, `PHASE2_TRIGGER_STATE`, `PHASE2_ACTIVE_STATE`, `PHASE2_COMPLETE_STATE`, `BLOCKED_STATE` with defaults
- [ ] 6.2 Add `PHASE1_COMMAND` and `PHASE2_COMMAND` env vars (defaults: "generate-spec", "implement-spec")
- [ ] 6.3 Update the Linear webhook event source to detect trigger states: map incoming state IDs to state names, compare against configured trigger states. Emit phase-tagged events (new field on `TaskStatusChangedEvent` or a new event type)
- [ ] 6.4 Update `LinearProvider.update_task_status` to support setting states by exact name (not just by state type). Add method to resolve configurable state names to state IDs

## 7. Orchestrator Fan-Out / Fan-In

- [ ] 7.1 Replace `_start_task` with `_start_phase(task_id, phase, repo_keys)` that fans out: for each repo_key, create a worktree (branch: `<identifier>/<repo_key>/<phase>`), create a task record, and spawn a Claude session
- [ ] 7.2 Update `ClaudeRunner` prompt building: instead of piping a description to stdin, construct the prompt as `"/<command-name> <ticket-identifier>"` where command name comes from `PHASE1_COMMAND` or `PHASE2_COMMAND`
- [ ] 7.3 Implement fan-in completion callback: `_check_phase_complete(issue_id, phase)` that queries the store, and if all tasks are done, transitions the Linear issue to the next state. Use an async lock per issue_id to prevent duplicate transitions
- [ ] 7.4 Update `_handle_blocked` to check if the issue should move to the "Blocked" board state (only if not already there)
- [ ] 7.5 Update `_handle_complete` to call `_check_phase_complete` instead of directly transitioning the issue. Remove the PR-creation logic from the orchestrator (Phase 2 custom command handles it)
- [ ] 7.6 Wire the active-state transition: when `_start_phase` fires, move the issue to `PHASE1_ACTIVE_STATE` or `PHASE2_ACTIVE_STATE`

## 8. Worktree Manager Updates

- [ ] 8.1 Update `WorktreeManager` to support phase-aware branch naming: `get_branch_name(identifier, repo_key, phase)` producing e.g. `eng-123/api/spec`
- [ ] 8.2 Create worktree managers dynamically per-task (keyed by repo path from REPO_MAP) instead of per-instance

## 9. Integration and Cleanup

- [ ] 9.1 Update `core/app.py` startup to load REPO_MAP, ALLOWED_ASSIGNEES, and phase config. Wire the new settings into the orchestrator
- [ ] 9.2 Update the legacy `tasks/store.py` and `tasks/manager.py` to align with the new schema or remove if fully superseded by `core/store.py`
- [ ] 9.3 Remove the old `config.py` (top-level `claudear/config.py`) if it duplicates `core/config.py`
- [ ] 9.4 Add end-to-end integration test: mock a webhook for "Ready for Spec" with two repo labels, verify two tasks created, mock both completing, verify issue moved to "Spec Review"
- [ ] 9.5 Add end-to-end integration test: mock a webhook for "Ready for Dev", verify Phase 2 tasks created with correct command invocation, mock completion, verify issue moved to "In Review"
- [ ] 9.6 Update README or configuration documentation with new env vars and workflow description
