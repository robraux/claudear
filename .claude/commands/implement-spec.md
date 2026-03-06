# Implement from Reviewed Spec

You are running Phase 2 of the Claudear spec-driven pipeline. Your job is to
implement code based on an approved OpenSpec change.

## Context

You are in a fresh git worktree for this repository. OpenSpec is initialized.
The Linear MCP server is available. An approved spec exists in
openspec/changes/ from a previous Phase 1 run.

## Steps

1. Read the Linear ticket to confirm the current state and find the spec
   location. The ticket identifier will be provided in your initial prompt.

2. Locate the OpenSpec change folder. List the contents of openspec/changes/
   and find the folder whose name starts with the ticket identifier (e.g.,
   TICKET-123-add-oauth). There should be exactly one active match (prior
   re-triggers clean up stale changes). Read:
   - tasks.md for the ordered implementation checklist
   - design.md for architectural decisions and patterns to follow
   - specs/ for detailed behavioral specifications

3. Apply the spec using OpenSpec:
   /opsx:apply <change-name>

   This reads tasks.md and implements each task sequentially, marking
   checkboxes as complete.

4. For each task:
   - Implement the change
   - Write or update tests
   - Run the test suite (use the repo's test command from CLAUDE.md)
   - If tests pass, make an atomic commit:
     git commit -m "<type>(<scope>): <description> [<ticket-identifier>]"
   - If tests fail, fix and recommit before moving to the next task

5. If you encounter a blocker (unclear requirement, missing dependency,
   conflicting patterns):
   - Post a comment to the Linear ticket explaining the blocker
   - Output the following as your message text:

   BLOCKED: <reason>

   Then stop and wait for human guidance.

6. After all tasks are complete:
   - Run the full test suite
   - Run linting and type checking
   - Verify implementation against spec artifacts manually:
     - Review tasks.md: confirm all checkboxes are checked
     - Review specs/: confirm each spec's requirements are implemented
     - Review design.md: confirm architectural decisions are reflected in code
     - Run tests with coverage to check for gaps
   - Address any issues found before proceeding.

7. Archive the OpenSpec change (BEFORE creating the PR):
   /opsx:archive <change-name>

   This merges delta specs into the main openspec/specs/ directory and
   moves the change folder to openspec/changes/archive/. Commit the
   result. The archive commit becomes part of the PR diff, so if the
   PR is rejected, the archive never reaches main.

8. Create a draft PR:
   gh pr create --draft \
     --title "<ticket-identifier>: <title>" \
     --body "Implements <ticket-identifier>\n\nOpenSpec change: <name>\n\n<summary of changes>"

9. Post the PR link to the Linear ticket as a comment:

   **Implementation Complete for <ticket-identifier>**

   PR: <link>

   **Changes**: <summary>
   **Tests**: <pass/fail count>
   **Verification**: <checklist results>

## Completion

When all steps above are done, output the following as your final message:

TASK_COMPLETE
