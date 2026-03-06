## ADDED Requirements

### Requirement: Phase 1 trigger on Ready for Spec
The system SHALL trigger Phase 1 (spec generation) when an issue transitions to the "Ready for Spec" board state. The trigger state name SHALL be configurable via `PHASE1_TRIGGER_STATE` (default: "Ready for Spec").

#### Scenario: Issue moves to Ready for Spec
- **WHEN** a webhook indicates an issue moved to the state matching `PHASE1_TRIGGER_STATE`
- **AND** the issue passes intake filtering
- **AND** the issue has at least one valid `repo:<key>` label
- **THEN** the system SHALL start Phase 1 for each target repo

#### Scenario: Issue moves to Ready for Spec with no repo labels
- **WHEN** a webhook indicates an issue moved to `PHASE1_TRIGGER_STATE`
- **AND** the issue has no `repo:<key>` labels
- **THEN** the system SHALL log a warning and take no action

### Requirement: Phase 2 trigger on Ready for Dev
The system SHALL trigger Phase 2 (implementation) when an issue transitions to the "Ready for Dev" board state. The trigger state name SHALL be configurable via `PHASE2_TRIGGER_STATE` (default: "Ready for Dev").

#### Scenario: Issue moves to Ready for Dev
- **WHEN** a webhook indicates an issue moved to the state matching `PHASE2_TRIGGER_STATE`
- **AND** the issue passes intake filtering
- **AND** the issue has at least one valid `repo:<key>` label
- **THEN** the system SHALL start Phase 2 for each target repo

### Requirement: Phase-specific Claude command invocation
Each phase SHALL invoke a Claude Code custom command in the target repo's worktree. The command name SHALL be configurable:
- Phase 1: `PHASE1_COMMAND` env var (default: "generate-spec")
- Phase 2: `PHASE2_COMMAND` env var (default: "implement-spec")

The system SHALL invoke Claude as: `claude --print -p "/<command-name> <ticket-identifier>" --verbose --output-format stream-json --dangerously-skip-permissions`

#### Scenario: Phase 1 invokes generate-spec command
- **WHEN** Phase 1 starts for issue ENG-123 in the "api" repo worktree
- **THEN** the system SHALL execute Claude with prompt `"/generate-spec ENG-123"` in the worktree directory
- **AND** stream JSONL output for tool-use monitoring and blocked/completion detection

#### Scenario: Phase 2 invokes implement-spec command
- **WHEN** Phase 2 starts for issue ENG-123 in the "api" repo worktree
- **THEN** the system SHALL execute Claude with prompt `"/implement-spec ENG-123"` in the worktree directory

#### Scenario: Custom command name configured
- **WHEN** `PHASE1_COMMAND=my-spec-gen` is set
- **THEN** the system SHALL invoke Claude with prompt `"/my-spec-gen ENG-123"` for Phase 1

### Requirement: Isolated worktree per phase per repo
Each phase execution SHALL create a fresh worktree from `main` in the target repo. Worktree branch names SHALL encode the issue identifier, repo key, and phase to avoid collisions.

#### Scenario: Phase 1 worktree creation
- **WHEN** Phase 1 starts for issue ENG-123 targeting repo "api"
- **THEN** the system SHALL create a worktree with branch name `eng-123/api/spec` (or similar encoding)
- **AND** the worktree SHALL be based on the `main` branch

#### Scenario: Phase 2 worktree is independent of Phase 1
- **WHEN** Phase 2 starts for issue ENG-123 targeting repo "api"
- **THEN** the system SHALL create a NEW worktree with branch name `eng-123/api/dev`
- **AND** the worktree SHALL be based on the `main` branch (not the Phase 1 branch)

### Requirement: Automated state transitions on phase completion
The system SHALL move the Linear issue to the next board state when all repo-tasks for a phase complete successfully.

| Trigger | On All Tasks Complete | On Any Task Blocked |
|---|---|---|
| Phase 1 | Move to "Spec Review" | Move to "Blocked" |
| Phase 2 | Move to "In Review" | Move to "Blocked" |

State names SHALL be configurable via env vars:
- `PHASE1_ACTIVE_STATE` (default: "Spec in Progress")
- `PHASE1_COMPLETE_STATE` (default: "Spec Review")
- `PHASE2_ACTIVE_STATE` (default: "Dev in Progress")
- `PHASE2_COMPLETE_STATE` (default: "In Review")
- `BLOCKED_STATE` (default: "Blocked")

#### Scenario: All Phase 1 tasks complete
- **WHEN** all repo-tasks for Phase 1 of an issue reach "completed" state
- **THEN** the system SHALL move the Linear issue to the state matching `PHASE1_COMPLETE_STATE`

#### Scenario: One Phase 1 task blocked
- **WHEN** any repo-task for Phase 1 of an issue enters "blocked" state
- **THEN** the system SHALL move the Linear issue to the state matching `BLOCKED_STATE`
- **AND** post a comment identifying which repo-task is blocked and why

#### Scenario: All Phase 2 tasks complete
- **WHEN** all repo-tasks for Phase 2 of an issue reach "completed" state
- **THEN** the system SHALL move the Linear issue to the state matching `PHASE2_COMPLETE_STATE`

### Requirement: Active state transition on phase start
When a phase begins, the system SHALL move the Linear issue to the corresponding "in progress" state.

#### Scenario: Phase 1 starts
- **WHEN** Phase 1 tasks are spawned for an issue
- **THEN** the system SHALL move the issue to the state matching `PHASE1_ACTIVE_STATE`

#### Scenario: Phase 2 starts
- **WHEN** Phase 2 tasks are spawned for an issue
- **THEN** the system SHALL move the issue to the state matching `PHASE2_ACTIVE_STATE`

### Requirement: Human approval gate between phases
The system SHALL NOT automatically transition from Phase 1 completion to Phase 2. The human operator MUST manually move the issue from "Spec Review" to "Ready for Dev" to approve the spec and trigger implementation.

#### Scenario: Phase 1 completes, waits for human
- **WHEN** Phase 1 completes and the issue moves to "Spec Review"
- **THEN** the system SHALL take no further action until the issue is moved to "Ready for Dev" by a human

### Requirement: Phase 2 completion creates PR via Claude command
The system SHALL NOT independently create PRs after Phase 2. PR creation is handled within the Phase 2 custom command (implement-spec). The system's post-completion responsibility is limited to transitioning the board state.

#### Scenario: Phase 2 task completes
- **WHEN** a Phase 2 Claude session exits successfully
- **THEN** the system SHALL mark the task as completed
- **AND** check if all sibling repo-tasks are done for fan-in
- **AND** the system SHALL NOT separately push branches or create PRs (the custom command handles this)
