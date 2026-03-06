## ADDED Requirements

### Requirement: Composite primary key for tasks
The task store SHALL use a composite primary key of `(issue_id, repo_key)` where `issue_id` is the Linear issue UUID and `repo_key` is the key from `REPO_MAP`. This allows multiple concurrent tasks per Linear issue.

#### Scenario: Two tasks for same issue, different repos
- **WHEN** issue ENG-123 has labels `repo:api` and `repo:web`
- **AND** Phase 1 is triggered
- **THEN** the store SHALL create two independent task records: one with key `(eng-123-uuid, api)` and one with `(eng-123-uuid, web)`
- **AND** each record SHALL have its own state, worktree path, branch name, and session ID

#### Scenario: Same issue and repo, different phases
- **WHEN** Phase 2 starts for issue ENG-123 targeting repo "api"
- **AND** a Phase 1 record already exists for `(eng-123-uuid, api)`
- **THEN** the store SHALL update the existing record with the new phase, resetting state to "pending"
- **AND** preserve the Phase 1 metadata (or store it in a history/log column)

### Requirement: Phase tracking column
Each task record SHALL include a `phase` column with value "spec" or "dev" indicating which pipeline phase the task is currently executing.

#### Scenario: Phase 1 task created
- **WHEN** a task is created for Phase 1
- **THEN** the `phase` column SHALL be set to "spec"

#### Scenario: Phase 2 task created
- **WHEN** a task is created for Phase 2
- **THEN** the `phase` column SHALL be set to "dev"

### Requirement: Repo key column
Each task record SHALL include a `repo_key` column storing the repo key from `REPO_MAP` that this task targets.

#### Scenario: Task record includes repo key
- **WHEN** a task is created for repo key "api"
- **THEN** the `repo_key` column SHALL be set to "api"

### Requirement: Query tasks by issue and phase
The store SHALL support querying all tasks for a given `issue_id` and `phase` combination, to support fan-in completion gating.

#### Scenario: Query all Phase 1 tasks for an issue
- **WHEN** the orchestrator queries tasks for issue "eng-123-uuid" with phase "spec"
- **THEN** the store SHALL return all task records matching that issue_id and phase
- **AND** each record SHALL include its current state

### Requirement: Fan-in completion check
The store SHALL provide a method to determine whether all tasks for a given `(issue_id, phase)` are in a completed state. This is the gating check for issue-level state transitions.

#### Scenario: All tasks complete
- **WHEN** issue ENG-123 has two Phase 1 tasks (api, web)
- **AND** both tasks are in "completed" state
- **THEN** the completion check SHALL return true

#### Scenario: One task still in progress
- **WHEN** issue ENG-123 has two Phase 1 tasks
- **AND** the "api" task is "completed" but the "web" task is "in_progress"
- **THEN** the completion check SHALL return false

#### Scenario: One task blocked
- **WHEN** issue ENG-123 has two Phase 1 tasks
- **AND** the "api" task is "blocked"
- **THEN** the completion check SHALL return false
- **AND** a separate "any blocked?" query SHALL return true with the blocked task's reason

### Requirement: Task key format
The `task_key` stored in the database SHALL follow the format `linear:<team>:<issue_uuid>:<repo_key>` to maintain uniqueness across provider, team, issue, and repo dimensions.

#### Scenario: Task key generation
- **WHEN** a task is created for Linear team "ENG", issue UUID "abc-123", repo key "api"
- **THEN** the task_key SHALL be "linear:ENG:abc-123:api"

### Requirement: Schema migration
The system SHALL include a migration that converts the existing single-key task table to the new composite-key schema. Since this is a pre-production tool, the migration MAY drop and recreate the table.

#### Scenario: Fresh database
- **WHEN** Claudear starts with no existing database
- **THEN** the system SHALL create the task table with the new schema including `repo_key`, `phase`, and composite primary key

#### Scenario: Existing database from prior version
- **WHEN** Claudear starts with an existing database using the old schema
- **THEN** the system SHALL detect the old schema and recreate the table
- **AND** log a warning that existing task data has been cleared

### Requirement: Concurrent access safety for fan-in
The store SHALL handle concurrent completion updates safely. When multiple repo-tasks complete simultaneously, the fan-in check MUST use locking to prevent duplicate issue-level state transitions.

#### Scenario: Two tasks complete at the same time
- **WHEN** tasks for "api" and "web" both complete within milliseconds of each other
- **THEN** only ONE fan-in check SHALL trigger the issue-level state transition
- **AND** the second completion SHALL observe the transition already happened and take no action
