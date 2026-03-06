## ADDED Requirements

### Requirement: Assignee allowlist filtering
The system SHALL accept an `ALLOWED_ASSIGNEES` environment variable containing a comma-separated list of Linear user IDs. Only issues assigned to a user in this list SHALL be processed.

#### Scenario: Issue assigned to allowed user
- **WHEN** a webhook fires for an issue assigned to user ID "user-abc"
- **AND** `ALLOWED_ASSIGNEES` contains "user-abc"
- **THEN** the system SHALL continue processing the event

#### Scenario: Issue assigned to disallowed user
- **WHEN** a webhook fires for an issue assigned to user ID "user-xyz"
- **AND** `ALLOWED_ASSIGNEES` does not contain "user-xyz"
- **THEN** the system SHALL drop the event silently
- **AND** log at DEBUG level that the issue was filtered out due to assignee

#### Scenario: Issue unassigned
- **WHEN** a webhook fires for an issue with no assignee
- **THEN** the system SHALL drop the event silently

#### Scenario: ALLOWED_ASSIGNEES not configured
- **WHEN** `ALLOWED_ASSIGNEES` is unset or empty
- **THEN** the system SHALL refuse to start and log an error indicating the variable is required

### Requirement: Claude auto label filtering
The system SHALL only process issues that carry the `claude:auto` label. Issues without this label SHALL be dropped before event dispatch.

#### Scenario: Issue has claude:auto label
- **WHEN** a webhook fires for an issue with the `claude:auto` label
- **THEN** the system SHALL continue processing the event

#### Scenario: Issue lacks claude:auto label
- **WHEN** a webhook fires for an issue without the `claude:auto` label
- **THEN** the system SHALL drop the event silently
- **AND** log at DEBUG level that the issue was filtered out due to missing label

### Requirement: Combined filter evaluation
The system SHALL require BOTH conditions (assignee in allowlist AND `claude:auto` label present) to pass before processing an issue. Filtering SHALL occur in the webhook event source layer, before any event is dispatched to the orchestrator.

#### Scenario: Both conditions met
- **WHEN** an issue is assigned to an allowed user AND has the `claude:auto` label
- **THEN** the system SHALL dispatch the event to the orchestrator

#### Scenario: Allowed assignee but no auto label
- **WHEN** an issue is assigned to an allowed user but lacks `claude:auto`
- **THEN** the system SHALL drop the event silently

#### Scenario: Auto label present but wrong assignee
- **WHEN** an issue has `claude:auto` but is assigned to a user not in the allowlist
- **THEN** the system SHALL drop the event silently

### Requirement: Filter applies only to state-change events
The intake filter SHALL apply to issue state-change webhooks (the trigger for task processing). Comment webhooks for already-tracked issues (e.g., unblocking a blocked task) SHALL NOT be subject to intake filtering.

#### Scenario: Comment on tracked blocked task bypasses filter
- **WHEN** a comment webhook fires for an issue that is already tracked as a blocked task
- **THEN** the system SHALL process the comment regardless of assignee or labels
