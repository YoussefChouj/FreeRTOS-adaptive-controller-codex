# Task T3b: finish the sim harness (fix round 1)

The original brief is `.agent-ops/tasks-src/t3-sim.md`. Read it and docs/research-platform/SPEC.md; both are binding.

The previous worker died before finishing. Its work is committed on branch `worker/20260924-061257` (commit ff7b227).
Step 0: in your worktree run `git merge --no-edit worker/20260924-061257`.

Current state: `python -m pytest ground_station/research/sim -q` gives 13 failures, including:
- TestDryRun::test_rmse
- TestReferenceModel::test_first_order_ref_model
- TestReferenceModel::test_ref_model_error

## Do
1. Run the sim tests. For each failure decide whether the code or the test is wrong, using the ORIGINAL project's sources as truth (the paths are in t3-sim.md).
   - Fix the code when the code is wrong.
   - Fix a test only when its expected value was invented; derive the replacement known answer analytically, and put the derivation in a comment.
   - Never weaken a test to make it pass (no loosening tolerances without a reason in the comment).
2. Check that every number in constants.py has a file:line citation into the original project. Remove or mark any uncited number as NOT FOUND.
3. Finish anything from the t3-sim.md Build list that is missing.

## Tools
Use only the tools listed as available to you. There is no `replace_in_files` tool; use `edit` or `bash`.

## Acceptance
- The full tree is green: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` (paste the last line).
- Append "## T3 as built" (at most 15 lines) to the SPEC, listing any constant NOT FOUND.
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Workspace rule (hard)
Work ONLY inside your own worktree (your starting cwd, under .worktrees/<id>). Never edit files in the main checkout. Commit on your branch there.
