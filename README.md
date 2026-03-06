# Claudear

Autonomous development automation with Claude Code and Linear.

Claudear watches your Linear board for state changes, creates isolated git worktrees, and invokes Claude Code headless to implement tickets. It supports a two-phase pipeline (spec generation, then implementation) with label-based multi-repo routing.

## Features

- **Two-Phase Pipeline**: Spec generation ("Ready for Spec") and implementation ("Ready for Dev"), separated by a human review gate
- **Label-Based Repo Routing**: Tag issues with `repo:api`, `repo:web`, etc. to route work to multiple repositories in parallel
- **Intake Filtering**: Only processes issues assigned to allowed users with the `claude:auto` label
- **Fan-Out / Fan-In**: One issue can spawn parallel tasks across repos; the ticket advances only when all repo tasks complete
- **Automated State Transitions**: Moves tickets through board states via the Linear API
- **Multi-Team**: Support multiple Linear teams simultaneously
- **Real-Time Labels**: Shows what Claude is doing (reading, editing, testing) via Linear labels

## Installation

```bash
pip install claudear
```

## Quick Start

```bash
# Create your config
cp .env.example .env
# Edit .env with your API keys (see Configuration below)

# Run
claudear
```

> **Important:** Always run `claudear` from the directory containing your `.env` file.

## How It Works

1. **Tag issue** with `claude:auto` and `repo:X` labels, assign to yourself
2. **Move to "Ready for Spec"** - Claudear runs `/<spec-command> <identifier>` in each tagged repo
3. **Review spec** - Issue moves to "Spec Review" when all repos finish. You review.
4. **Move to "Ready for Dev"** - Claudear runs `/<impl-command> <identifier>` in each repo
5. **Review code** - Issue moves to "In Review" when all repos finish
6. **Blocked?** - If Claude gets stuck, the issue moves to "Blocked" and a comment is posted. Reply to unblock.

## Prerequisites

- Python 3.9+
- [Claude Code](https://claude.ai/code) CLI installed and authenticated
- [ngrok](https://ngrok.com/) account (free tier works) for webhook delivery
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
- Linear workspace with API access

## Configuration

### Environment Variables

```bash
# Linear
LINEAR_API_KEY=lin_api_xxx           # Settings -> API -> Personal API keys
LINEAR_WEBHOOK_SECRET=whsec_xxx      # Created when you register the webhook
LINEAR_TEAM_IDS=ENG                  # Comma-separated team keys

# Intake filter
ALLOWED_ASSIGNEES=user-uuid-1,user-uuid-2   # Linear user IDs allowed to trigger automation

# Repo routing (JSON: label key -> local path)
REPO_MAP='{"api": "/path/to/api-repo", "web": "/path/to/web-repo"}'

# Two-phase pipeline states (must match your Linear board exactly)
PHASE1_TRIGGER_STATE=Ready for Spec
PHASE1_ACTIVE_STATE=Speccing
PHASE1_COMPLETE_STATE=Spec Review
PHASE2_TRIGGER_STATE=Ready for Dev
PHASE2_ACTIVE_STATE=In Progress
PHASE2_COMPLETE_STATE=In Review
BLOCKED_STATE=Blocked

# Phase commands (Claude Code custom commands invoked as /<command> <identifier>)
PHASE1_COMMAND=generate-spec
PHASE2_COMMAND=implement-spec

# GitHub
GITHUB_TOKEN=ghp_xxx

# Server & ngrok
WEBHOOK_PORT=8000
NGROK_AUTHTOKEN=xxx

# Optional
LINEAR_LABELS_ENABLED=true           # Real-time activity labels (default: true)
MAX_CONCURRENT_TASKS=5
COMMENT_POLL_INTERVAL=30             # Seconds between polls for blocked tasks
BLOCKED_TIMEOUT=86400                # Seconds before blocked task times out
LOG_LEVEL=INFO
DB_PATH=claudear.db
```

### Linear Board Setup

Your Linear workflow should have these states (names configurable via env vars):

```
Backlog -> Ready for Spec -> Speccing -> Spec Review -> Ready for Dev -> In Progress -> In Review -> Done
                                                    \                                            /
                                                     +-----------> Blocked <--------------------+
```

### Labels

Create these labels in your Linear workspace:

- `claude:auto` - Required on issues for Claudear to pick them up
- `repo:api`, `repo:web`, etc. - One per repo key in your `REPO_MAP`

## Setup

### 1. Create a static ngrok domain

1. Go to [ngrok Domains](https://dashboard.ngrok.com/cloud-edge/domains)
2. Create a free static domain (e.g., `your-name.ngrok-free.app`)

### 2. Disable Linear's GitHub automations

Linear has built-in automations that conflict with Claudear. **You must disable them:**

1. Linear -> Settings -> Team Settings -> Workflow -> **GitHub**
2. Set all "Automate state changes" to **No action**

### 3. Register Linear webhook

1. Linear -> Settings -> API -> Webhooks -> **Create webhook**
2. URL: `https://your-name.ngrok-free.app/webhooks/linear`
3. Events: Issues, Comments
4. Copy the signing secret to `LINEAR_WEBHOOK_SECRET`

### 4. Run

```bash
claudear
```

## Troubleshooting

**Webhook not receiving events**
- Verify webhook URL matches your ngrok domain
- Check signing secret matches `LINEAR_WEBHOOK_SECRET`
- Test: `curl https://your-domain.ngrok-free.app/health`

**Issues not picked up**
- Verify the issue has the `claude:auto` label
- Verify the assignee's user ID is in `ALLOWED_ASSIGNEES`
- Verify at least one `repo:X` label matches a key in `REPO_MAP`

**Claude not starting**
- Run `claude` manually to verify CLI is installed and authenticated
- Check that all paths in `REPO_MAP` exist and are git repositories

**Tasks stuck in "Blocked"**
- Check Linear for Claude's comment asking for help
- Reply to unblock (polls every 30 seconds by default)

## How It Uses Claude Code

Claudear runs Claude Code CLI in headless mode using your **Claude Code subscription** (not API credits). It invokes custom commands (`/<command> <identifier>`) in isolated git worktrees.

## License

MIT
