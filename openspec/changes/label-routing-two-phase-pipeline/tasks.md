## 1. Test Infrastructure

- [x] 1.1 Create `tests/` directory with `__init__.py` and `conftest.py`
- [x] 1.2 Add shared fixtures to `conftest.py`: temp SQLite database, mock `LinearClient` (patch `_query` to return canned responses), mock webhook payload factory, sample `REPO_MAP` dict, sample `ALLOWED_ASSIGNEES` list
- [x] 1.3 Add a `tests/factories.py` with helpers to build `TaskRecord`, `TaskId`, `WebhookPayload`, and issue data dicts with sensible defaults and overridable fields
- [x] 1.4 Verify pytest runs and discovers the test directory (`pytest --collect-only`)

## 2. Remove Notion Provider

- [x] 2.1 Delete `claudear/providers/notion/` directory (client.py, poller.py, provider.py, __init__.py)
- [x] 2.2 Remove Notion config from `MultiProviderSettings` in `core/config.py` (notion_api_key, notion_database_ids, notion_poll_interval, get_notion_database_ids, get_notion_instance, has_notion, and related validation)
- [x] 2.3 Remove Notion references from `core/app.py`, `unified_main.py`, and any startup/registration code
- [x] 2.4 Remove Notion from `ProviderType` enum if it won't break existing DB records (or leave as deprecated)
- [x] 2.5 Remove Notion references from `scripts/test_multi_provider.py` or delete the script if it's fully replaced by the new test suite
- [x] 2.6 Verify the application starts cleanly without Notion configuration

## 3. Repo Map Configuration

- [x] 3.1 Add `REPO_MAP` env var to `MultiProviderSettings` as a JSON string field. Parse it into a `dict[str, Path]` with a property method
- [x] 3.2 Add startup validation: REPO_MAP is present, valid JSON, each path exists and is a git repo. Log warnings for invalid paths, error if no valid entries
- [x] 3.3 Remove `ProviderInstance.repo_path`, `get_instance_repo_path`, and per-team `LINEAR_<TEAM>_REPO` env var resolution from config
- [x] 3.4 Update `InstanceResources` and `register_instance` in the orchestrator to work without per-instance repo paths (worktree managers will be created per-task, not per-instance)
- [x] 3.5 Add `tests/test_config.py`: test REPO_MAP parsing with valid JSON, invalid JSON, missing paths, empty map, and that legacy `LINEAR_<TEAM>_REPO` vars are ignored when REPO_MAP is set

## 4. Intake Filter

- [x] 4.1 Add `ALLOWED_ASSIGNEES` env var to `MultiProviderSettings` as a comma-separated string. Add startup validation (required, non-empty)
- [x] 4.2 Add intake filter logic to `LinearWebhookEventSource._handle_issue_webhook`: fetch full issue from Linear API, check assignee ID against allowlist, check for `claude:auto` label
- [x] 4.3 Ensure filter only applies to state-change events, not comment webhooks for tracked tasks
- [x] 4.4 Add DEBUG-level logging for filtered-out issues (reason: assignee, label, or both)
- [x] 4.5 Add `tests/test_intake_filter.py`: allowed assignee + auto label passes, missing label drops, wrong assignee drops, unassigned drops, both conditions fail drops, comment on tracked task bypasses filter

## 5. Label-Based Repo Resolution

- [x] 5.1 Add a `resolve_repo_labels(issue_id: str) -> list[str]` method that fetches the full issue from Linear API and extracts `repo:<key>` labels, returning resolved keys
- [x] 5.2 Add validation: warn and skip unknown keys, warn and skip if no repo labels found
- [x] 5.3 Integrate repo resolution into the webhook handler: on trigger-state events, resolve repos before dispatching to orchestrator. Pass `repo_keys` list alongside the event
- [x] 5.4 Add `tests/test_repo_routing.py`: single repo label, multiple repo labels, unknown key skipped, no repo labels returns empty, mixed valid and invalid keys

## 6. Multi-Task Store

- [x] 6.1 Update the `tasks` table schema: change primary key to composite `(issue_id, repo_key)`, add `repo_key TEXT NOT NULL` and `phase TEXT NOT NULL` columns, update `task_key` format to `linear:<team>:<issue_uuid>:<repo_key>:<phase>`
- [x] 6.2 Add schema migration logic: detect old schema on startup, drop and recreate table, log warning about data loss. Add a `schema_version` table
- [x] 6.3 Update `TaskRecord` dataclass: add `repo_key` and `phase` fields
- [x] 6.4 Update `TaskStore.save()`, `get()`, `get_by_key()` to use the new composite key
- [x] 6.5 Add `get_by_issue_and_phase(issue_id: str, phase: str) -> list[TaskRecord]` query method for fan-in
- [x] 6.6 Add `check_phase_complete(issue_id: str, phase: str) -> tuple[bool, Optional[str]]` that returns `(all_complete, blocked_reason)` - true if all tasks for that issue+phase are completed, or the reason if any are blocked
- [x] 6.7 Update the unified `core/store.py` (TaskStore) to match - same composite key, repo_key, phase columns
- [x] 6.8 Add `tests/test_task_store.py`: create two tasks for same issue with different repo keys, query by issue+phase, fan-in check with all complete, fan-in check with one blocked, fan-in check with one in-progress, schema migration from old to new format

## 7. Two-Phase Pipeline State Machine

- [x] 7.1 Add phase-related board state configuration to `MultiProviderSettings`: `PHASE1_TRIGGER_STATE`, `PHASE1_ACTIVE_STATE`, `PHASE1_COMPLETE_STATE`, `PHASE2_TRIGGER_STATE`, `PHASE2_ACTIVE_STATE`, `PHASE2_COMPLETE_STATE`, `BLOCKED_STATE` with defaults
- [x] 7.2 Add `PHASE1_COMMAND` and `PHASE2_COMMAND` env vars (defaults: "generate-spec", "implement-spec")
- [x] 7.3 Update the Linear webhook event source to detect trigger states: map incoming state IDs to state names, compare against configured trigger states. Emit phase-tagged events (new field on `TaskStatusChangedEvent` or a new event type)
- [x] 7.4 Update `LinearProvider.update_task_status` to support setting states by exact name (not just by state type). Add method to resolve configurable state names to state IDs
- [x] 7.5 Add `tests/test_phase_detection.py`: webhook with "Ready for Spec" state emits Phase 1 event, webhook with "Ready for Dev" emits Phase 2 event, webhook with unrelated state is ignored, custom trigger state names work

## 8. Orchestrator Fan-Out / Fan-In

- [x] 8.1 Replace `_start_task` with `_start_phase(task_id, phase, repo_keys)` that fans out: for each repo_key, create a worktree (branch: `<identifier>/<repo_key>/<phase>`), create a task record, and spawn a Claude session
- [x] 8.2 Update `ClaudeRunner` prompt building: instead of piping a description to stdin, construct the prompt as `"/<command-name> <ticket-identifier>"` where command name comes from `PHASE1_COMMAND` or `PHASE2_COMMAND`
- [x] 8.3 Implement fan-in completion callback: `_check_phase_complete(issue_id, phase)` that queries the store, and if all tasks are done, transitions the Linear issue to the next state. Use an async lock per issue_id to prevent duplicate transitions
- [x] 8.4 Update `_handle_blocked` to check if the issue should move to the "Blocked" board state (only if not already there)
- [x] 8.5 Update `_handle_complete` to call `_check_phase_complete` instead of directly transitioning the issue. Remove the PR-creation logic from the orchestrator (Phase 2 custom command handles it)
- [x] 8.6 Wire the active-state transition: when `_start_phase` fires, move the issue to `PHASE1_ACTIVE_STATE` or `PHASE2_ACTIVE_STATE`
- [x] 8.7 Add `tests/test_orchestrator.py`: fan-out creates N tasks for N repo keys, fan-in waits for all tasks before transitioning, fan-in with one blocked moves issue to Blocked, duplicate completion doesn't double-transition, correct Claude command is invoked per phase

## 9. Worktree Manager Updates

- [x] 9.1 Update `WorktreeManager` to support phase-aware branch naming: `get_branch_name(identifier, repo_key, phase)` producing e.g. `eng-123/api/spec`
- [x] 9.2 Create worktree managers dynamically per-task (keyed by repo path from REPO_MAP) instead of per-instance
- [x] 9.3 Add `tests/test_worktree.py`: branch name generation includes repo key and phase, different phases produce different branch names for same issue+repo

## 10. Integration and Cleanup

- [x] 10.1 Update `core/app.py` startup to load REPO_MAP, ALLOWED_ASSIGNEES, and phase config. Wire the new settings into the orchestrator
- [x] 10.2 Update the legacy `tasks/store.py` and `tasks/manager.py` to align with the new schema or remove if fully superseded by `core/store.py`
- [x] 10.3 Remove the old `config.py` (top-level `claudear/config.py`) if it duplicates `core/config.py`
- [x] 10.4 Add `tests/test_integration.py`: mock a webhook for "Ready for Spec" with two repo labels, verify two tasks created, mock both Claude sessions completing, verify issue moved to "Spec Review"
- [x] 10.5 Add `tests/test_integration.py`: mock a webhook for "Ready for Dev", verify Phase 2 tasks created with correct command invocation, mock completion, verify issue moved to "In Review"
- [x] 10.6 Add `tests/test_integration.py`: mock a webhook for "Ready for Spec" where one task gets blocked, verify issue moves to "Blocked" and comment posted identifying which repo
- [x] 10.7 Update README or configuration documentation with new env vars and workflow description
