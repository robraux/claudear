## ADDED Requirements

### Requirement: Repo map configuration
The system SHALL accept a `REPO_MAP` environment variable containing a JSON object mapping repo keys to local filesystem paths. Each key is a short identifier (e.g., "api", "web") and each value is an absolute path to a git repository.

#### Scenario: Valid repo map loaded at startup
- **WHEN** Claudear starts with `REPO_MAP={"api":"/home/user/repos/api","web":"/home/user/repos/web"}`
- **THEN** the system registers two repo targets keyed as "api" and "web"
- **AND** validates that each path exists and is a git repository

#### Scenario: Missing or invalid repo map
- **WHEN** Claudear starts with `REPO_MAP` unset or containing invalid JSON
- **THEN** the system SHALL log an error and refuse to start

#### Scenario: Repo path does not exist
- **WHEN** `REPO_MAP` contains a key whose path does not exist on the filesystem
- **THEN** the system SHALL log a warning for that key and exclude it from available targets
- **AND** continue starting with the remaining valid entries

### Requirement: Label-based repo resolution
The system SHALL extract `repo:<key>` labels from a Linear issue and resolve each key against the `REPO_MAP` to determine target repositories. Labels not matching the `repo:` prefix SHALL be ignored for routing purposes.

#### Scenario: Single repo label
- **WHEN** an issue has the label `repo:api`
- **AND** "api" exists in `REPO_MAP`
- **THEN** the system resolves to a single target repository at the path mapped to "api"

#### Scenario: Multiple repo labels
- **WHEN** an issue has labels `repo:api` and `repo:web`
- **AND** both keys exist in `REPO_MAP`
- **THEN** the system resolves to two target repositories and SHALL spawn independent tasks for each

#### Scenario: Repo label with unknown key
- **WHEN** an issue has the label `repo:unknown`
- **AND** "unknown" does not exist in `REPO_MAP`
- **THEN** the system SHALL log a warning and skip that label
- **AND** process any remaining valid `repo:` labels on the issue

#### Scenario: No repo labels on issue
- **WHEN** a trigger-state webhook fires for an issue with no `repo:<key>` labels
- **THEN** the system SHALL log a warning and take no action for that issue

### Requirement: Full issue fetch for label resolution
The system SHALL fetch the complete issue (including all labels) from the Linear API when a trigger-state webhook fires, rather than relying solely on webhook payload data for label extraction.

#### Scenario: Webhook triggers full issue fetch
- **WHEN** a webhook indicates an issue moved to a trigger state ("Ready for Spec" or "Ready for Dev")
- **THEN** the system SHALL call the Linear API to fetch the full issue with labels before extracting `repo:<key>` labels
- **AND** use the API response (not the webhook payload) as the source of truth for labels

### Requirement: Removal of team-to-repo mapping
The system SHALL NOT use `ProviderInstance.repo_path` or per-team `LINEAR_<TEAM>_REPO` environment variables for repo resolution. All repo resolution SHALL go through the `REPO_MAP` + label mechanism.

#### Scenario: Legacy repo config ignored
- **WHEN** `LINEAR_ENG_REPO` is set in the environment alongside `REPO_MAP`
- **THEN** the system SHALL use `REPO_MAP` for repo resolution and ignore `LINEAR_ENG_REPO`
