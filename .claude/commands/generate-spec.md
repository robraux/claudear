# Generate Spec from Linear Ticket

You are running Phase 1 of the Claudear spec-driven pipeline. Your job is to
generate a complete OpenSpec change proposal from a Linear ticket.

## Context

You are in a git worktree for this repository. OpenSpec is initialized with
the custom profile (expanded command set). The Linear MCP server is available
for reading ticket details.

## Steps

1. Read the Linear ticket details.
   The ticket identifier will be provided in your initial prompt by Claudear.
   Use the Linear MCP tools to fetch the full ticket: title, description,
   comments, labels, and any linked issues.

2. Assess ticket clarity. Based on the ticket description and any comments,
   determine whether requirements are clear or need investigation.

3. Check for an existing OpenSpec change for this ticket. List the contents
   of openspec/changes/ and look for a folder whose name starts with the
   ticket identifier.

   IF NO EXISTING CHANGE (first run):
   /opsx:new <ticket-identifier>-<short-description>

   IF AN EXISTING CHANGE EXISTS (re-trigger after feedback):
   The human moved the ticket back to "Ready for Spec" to request changes.
   Check the latest comments on the Linear ticket for feedback. Use the
   existing change name. Do NOT run /opsx:new again.
   If the human deleted specific artifact files (to trigger regeneration),
   those will be missing and /opsx:ff will regenerate them.
   If no files were deleted, you need to regenerate based on the feedback.
   Delete the downstream artifacts that the feedback invalidates, then
   proceed to step 4.

4. Generate planning artifacts using one of two paths:

   PATH A (clear requirements, most tickets):
   /opsx:ff <change-name>
   This generates all artifacts (proposal.md, specs/, design.md, tasks.md)
   in one pass. It skips artifacts that already exist (file present = done)
   and generates only missing ones. On a re-trigger where the human deleted
   stale downstream artifacts, ff regenerates from the corrected upstream.

   PATH B (unclear, complex, or investigative tickets):
   /opsx:explore <change-name>
   Then iteratively: /opsx:continue <change-name>
   This builds artifacts one at a time following the dependency graph
   (proposal -> specs -> design -> tasks), letting you refine as you go.

   Use PATH B when: the ticket is vague, requires architectural decisions,
   involves debugging/investigation, or has competing approaches.

5. Review the generated artifacts for completeness:
   - Does the proposal capture the ticket's intent?
   - Are the specs specific enough for implementation?
   - Does the design reference existing codebase patterns?
   - Are the tasks ordered and atomic?

6. Post a summary to the Linear ticket as a comment. Format:

   **Spec Generated for <ticket-identifier>**

   **Proposal**: <one-paragraph summary from proposal.md>

   **Design approach**: <key decisions from design.md>

   **Tasks** (<count> tasks):
   <numbered list from tasks.md>

   **Artifacts location**: `openspec/changes/<name>/`

   ---
   *Review this spec. When ready, move to Ready for Dev to start implementation.*

7. Commit the OpenSpec artifacts to the worktree branch:
   git add openspec/changes/
   git commit -m "spec: generate OpenSpec artifacts for <ticket-identifier>"
   git push origin <branch>

## If Blocked

If at any point you encounter a blocker (unclear ticket, missing context,
ambiguous requirements, inaccessible resources), output the following as your
message text:

BLOCKED: <reason>

Then stop and wait for human guidance.

## Completion

Do not implement anything. Phase 1 is spec generation only.
When all steps above are done, output the following as your final message:

TASK_COMPLETE
