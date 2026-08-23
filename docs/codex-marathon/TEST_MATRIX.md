# Requirement-to-test matrix

This matrix maps the acceptance criteria in `docs/USER_STORIES.md` and every
named validation rule in `docs/SCHEMA.md` to requirement-level tests. Test ids
are pytest node ids relative to the repository root. A fixture or test name did
not count as coverage unless the assertions actually proved the criterion.

## Owner user stories

| Story | Acceptance criterion | Test evidence |
|---|---|---|
| US-001 | Repository and intentional non-repository projects are representable. | `tests/test_model.py::test_repo_and_non_repo_projects_are_both_representable` |
| US-001 | Forks and duplicate checkouts are distinguishable. | `tests/test_model.py::test_fork_flag_and_duplicate_relationship_distinguish_copies` |
| US-001 | Entries show purpose, lifecycle, activity, visibility, and last review. | `tests/test_model.py::test_summary_exposes_the_six_listing_fields` |
| US-002 | GitHub sync cannot overwrite human-curated purpose. | `tests/test_sync.py::test_sync_never_writes_under_the_registry_directory` |
| US-002 | Unknown purpose may use the `needs_review` placeholder. | `tests/test_validation.py::test_needs_review_downgrades_missing_purpose_to_a_suggestion` |
| US-002 | Public-safe and private descriptions remain separate. | `tests/test_model.py::test_private_and_public_descriptions_are_separate_fields` |
| US-003 | Multiple dated accomplishments are supported. | `tests/test_model.py::test_accomplishments_sort_newest_first` |
| US-003 | Accomplishments can retain links to artifacts, demos, commits, releases, and PRs. | `tests/test_model.py::test_accomplishment_links_round_trip` |
| US-003 | A concise accomplishment summary is available. | `tests/test_model.py::test_accomplishment_summary_respects_its_limit` |
| US-004 | Active status and lifecycle remain separate. | `tests/test_validation.py::test_archived_but_active_is_a_suggestion_not_an_error` |
| US-004 | Too many `now` projects produces a warning. | `tests/test_validation.py::test_too_many_now_projects_is_a_suggestion` |
| US-004 | Lifecycle changes are intentional and reviewable. | `tests/test_proposals.py::test_proposal_records_exact_before_and_after_values`; `tests/test_proposals.py::test_applying_without_approval_is_refused` |
| US-005 | Missing next actions are reported. | `tests/test_queries.py::test_missing_next_actions_are_reported` |
| US-005 | Vague next actions are surfaced. | `tests/test_validation.py::test_vague_next_action_is_flagged_as_a_suggestion` |
| US-005 | A next action carries a review date and optional issue/PR link. | `tests/test_queries.py::test_list_next_actions_includes_review_date_and_link` |
| US-006 | Work can be filtered by lifecycle, category, priority, effort, or blocker. | `tests/test_queries.py::test_filters_compose`; `tests/test_queries.py::test_blocked_filter`; `tests/test_queries.py::test_work_queue_respects_project_filters` |
| US-006 | Human priority and GitHub urgency remain separate. | `tests/test_queries.py::test_work_queue_keeps_human_priority_and_github_urgency_separate` |
| US-006 | Every work item explains why it appears. | `tests/test_queries.py::test_work_queue_item_lists_every_contributing_reason` |
| US-007 | Directional relationships have the correct inverses. | `tests/test_model.py::test_relationship_inverses_pair_up_and_symmetric_kinds_self_invert`; `tests/test_queries.py::test_related_projects_include_inferred_inverse_edges` |
| US-007 | A superseded project must name a successor. | `tests/test_validation.py::test_superseded_without_successor_is_an_error`; `tests/test_validation.py::test_superseded_with_successor_passes` |
| US-007 | Broken relationship references are detected. | `tests/test_validation.py::test_broken_relationship_target_is_an_error` |
| US-008 | Staleness uses human review and GitHub activity independently. | `tests/test_queries.py::test_review_queue_triggers_on_stale_human_review`; `tests/test_queries.py::test_review_queue_triggers_on_stale_github_activity` |
| US-008 | Building the review queue never changes lifecycle. | `tests/test_queries.py::test_review_queue_leaves_the_registry_untouched` |
| US-008 | A review can update purpose, lifecycle, next action, and notes. | `tests/test_proposals.py::test_record_review_can_update_the_reviewed_fields` |
| US-009 | Export contains only explicitly approved public-safe fields. | `tests/test_portfolio.py::test_export_never_leaks_private_fields`; `tests/test_portfolio.py::test_exported_records_contain_only_allowlisted_keys` |
| US-009 | Private repository names and details are excluded by default. | `tests/test_portfolio.py::test_private_repo_name_is_withheld`; `tests/test_portfolio.py::test_repo_is_withheld_when_there_is_no_snapshot` |
| US-009 | Showcase ordering is human-curated. | `tests/test_portfolio.py::test_ordering_is_human_curated` |
| US-010 | Open PR rows include repo, title, draft, age, review, and CI state. | `tests/test_signals.py::test_open_prs_report_the_six_required_fields` |
| US-010 | Draft and ready PRs filter separately. | `tests/test_signals.py::test_draft_filter_partitions_pull_requests` |
| US-010 | Cached PR results show their refresh time. | `tests/test_signals.py::test_cached_results_carry_their_refresh_time`; `tests/test_cli.py::test_prs_command_reports_evidence_age` |
| US-011 | Attention includes failing CI, requested changes, stale/unreviewed PRs, and selected issues. | `tests/test_signals.py::test_every_attention_rule_fires_on_its_fixture` |
| US-011 | Every attention signal links to its GitHub source. | `tests/test_signals.py::test_every_attention_item_links_to_its_source` |
| US-011 | Every signal identifies and explains its rule. | `tests/test_signals.py::test_attention_items_explain_the_rule_that_produced_them`; `tests/test_cli.py::test_attention_command_explains_each_signal` |
| US-012 | Missing repo, archived/activity, visibility, and review-age mismatches are detected. | `tests/test_signals.py::test_missing_repository_is_an_error`; `tests/test_signals.py::test_archived_in_registry_but_pushed_on_github_is_an_error`; `tests/test_signals.py::test_private_in_registry_but_public_on_github_is_an_error`; `tests/test_signals.py::test_active_project_without_recent_review_is_a_suggestion` |
| US-012 | Sync reports mismatches without resolving them. | `tests/test_signals.py::test_mismatches_are_reported_not_resolved` |
| US-012 | Mismatch errors and suggestions remain distinct. | `tests/test_signals.py::test_private_in_registry_but_public_on_github_is_an_error`; `tests/test_signals.py::test_public_in_registry_but_private_on_github_is_a_suggestion`; `tests/test_cli.py::test_mismatches_exit_nonzero_on_errors` |

## MCP client stories

| Story | Expected capability or criterion | Test evidence |
|---|---|---|
| MCP-001 | `list_projects` supports lifecycle, activity, and category filters. | `tests/test_mcp.py::test_list_projects_and_filters` |
| MCP-001 | `get_project` returns curated, relationship, and GitHub context. | `tests/test_mcp.py::test_get_project_includes_relationships_and_github_state`; `tests/test_mcp.py::test_get_project_unknown_id_is_a_structured_tool_error` |
| MCP-001 | `search_projects` supports text queries. | `tests/test_mcp.py::test_search_projects` |
| MCP-001 | `list_related_projects` returns relationship queries. | `tests/test_mcp.py::test_list_related_projects_returns_inverse_edges` |
| MCP-002 | `list_next_actions` is callable and timestamped. | `tests/test_mcp.py::test_every_response_carries_source_timestamps` |
| MCP-002 | `find_missing_next_actions` and `list_projects_needing_review` return their queues. | `tests/test_mcp.py::test_missing_next_actions_and_review_queue` |
| MCP-002 | `get_attention_queue` combines work and preserves reasons. | `tests/test_mcp.py::test_attention_queue_items_carry_reasons` |
| MCP-002 | Recommendation tools carry source timestamps. | `tests/test_mcp.py::test_every_response_carries_source_timestamps` |
| MCP-002 | Missing human priority remains null rather than invented. | `tests/test_mcp.py::test_attention_queue_reports_null_for_unrecorded_priority` |
| MCP-003 | `list_open_prs` and `get_pr_attention` expose cached GitHub work. | `tests/test_mcp.py::test_list_open_prs_and_attention` |
| MCP-003 | `list_selected_issues` exposes watched issues. | `tests/test_mcp.py::test_selected_issues_tool` |
| MCP-003 | `get_github_sync_status` exposes refresh status. | `tests/test_mcp.py::test_sync_status_and_mismatches` |
| MCP-004 | GitHub refresh permits only REST GET and guarded read-query POST. | `tests/test_sync.py::test_client_only_ever_issues_get_requests`; `tests/test_client.py::test_post_is_allowlisted_only_inside_graphql`; `tests/test_client.py::test_graphql_refuses_non_read_operations_before_transport` |
| MCP-004 | Refresh records start, completion, errors, and coverage. | `tests/test_sync.py::test_sync_records_start_completion_coverage_and_errors` |
| MCP-004 | Partial failure keeps and marks last-known-good evidence. | `tests/test_sync.py::test_partial_failure_keeps_previous_data_and_marks_it_stale` |
| MCP-005 | Propose and apply are separate operations. | `tests/test_proposals.py::test_proposing_does_not_modify_the_project`; `tests/test_mcp.py::test_apply_requires_approved_true` |
| MCP-005 | Exact before/after values are visible before approval. | `tests/test_proposals.py::test_proposal_records_exact_before_and_after_values`; `tests/test_mcp.py::test_proposals_can_be_listed_and_inspected` |
| MCP-005 | Changes are schema/registry validated. | `tests/test_proposals.py::test_apply_refuses_a_change_that_would_break_validation`; `tests/test_proposals.py::test_apply_refuses_a_value_the_schema_rejects` |
| MCP-005 | Applied changes are auditable. | `tests/test_proposals.py::test_every_apply_appends_one_audit_record`; `tests/test_proposals.py::test_refused_apply_writes_no_audit_record` |
| MCP-005 | `record_project_review` also requires explicit approval. | `tests/test_mcp.py::test_record_review_without_true_approval_is_an_unmistakable_error`; `tests/test_mcp.py::test_record_review_with_true_approval_applies` |
| MCP-006 | Initial tools expose no GitHub merge/delete/archive/visibility/issue-close mutation. | `tests/test_mcp.py::test_no_tool_name_suggests_an_external_mutation`; `tests/test_mcp.py::test_the_only_write_tools_target_the_registry_itself` |
| MCP-006 | A later external write must identify its repository and target. | Conditional requirement: no external write tool exists; `tests/test_mcp.py::test_the_only_write_tools_target_the_registry_itself` keeps the condition absent. A future tool must add its own target-schema test. |
| MCP-006 | Destructive/public-facing writes require explicit confirmation. | `tests/test_mcp.py::test_apply_and_review_tools_require_approval_in_their_schema`; no external destructive/public-facing tool currently exists. |

## Schema validation rules

| Rule | Declared trigger | Test evidence |
|---|---|---|
| `purpose_missing` | No purpose and no `needs_review`. | `tests/test_validation.py::test_missing_purpose_is_an_error` |
| `purpose_pending` | `needs_review: true` with no purpose. | `tests/test_validation.py::test_needs_review_downgrades_missing_purpose_to_a_suggestion` |
| `next_action_missing` | Active project has no next action. | `tests/test_validation.py::test_active_project_without_next_action_is_an_error`; `tests/test_validation.py::test_active_project_with_next_action_passes` |
| `next_action_unreviewed` | Next action has no reviewed date. | `tests/test_validation.py::test_next_action_without_a_reviewed_date_is_an_error` |
| `next_action_future_review` | Reviewed date is in the future. | `tests/test_validation.py::test_future_next_action_review_is_an_error_but_today_is_valid` |
| `next_action_vague` | Next action names no outcome or decision. | `tests/test_validation.py::test_vague_next_action_is_flagged_as_a_suggestion` |
| `now_without_outcome` | `now` project has no desired outcome. | `tests/test_validation.py::test_now_without_desired_outcome_is_an_error` |
| `now_overloaded` | More than three projects are in `now`. | `tests/test_validation.py::test_too_many_now_projects_is_a_suggestion` |
| `active_but_closed` | Active project is archived/superseded. | `tests/test_validation.py::test_archived_but_active_is_a_suggestion_not_an_error` |
| `inactive_but_committed` | Inactive project is `now`, `next`, or `maintained`. | `tests/test_validation.py::test_inactive_committed_lifecycles_are_suggestions` |
| `active_never_reviewed` | Active project has no review date. | `tests/test_validation.py::test_active_never_reviewed_is_a_suggestion_but_inactive_is_not` |
| `active_review_stale` | Active project review is older than 45 days. | `tests/test_validation.py::test_stale_review_on_active_project_is_a_suggestion` |
| `relationship_broken` | Relationship target is unregistered. | `tests/test_validation.py::test_broken_relationship_target_is_an_error` |
| `relationship_self_reference` | Project relates to itself. | `tests/test_validation.py::test_self_relationship_is_an_error` |
| `superseded_without_successor` | Superseded project has no successor. | `tests/test_validation.py::test_superseded_without_successor_is_an_error`; `tests/test_validation.py::test_superseded_with_successor_passes` |
| `overlap_same_repo` | Two projects share one repo. | `tests/test_validation.py::test_projects_sharing_a_repo_are_flagged_for_overlap`; `tests/test_validation.py::test_declared_duplicate_suppresses_the_overlap_suggestion` |
| `overlap_similar_name` | Two projects have identical normalized name tokens. | `tests/test_validation.py::test_identical_names_are_flagged_for_overlap` |
| `public_without_public_safe` | Public project lacks a public-safe description. | `tests/test_validation.py::test_public_without_public_safe_description_is_an_error` |
| `showcase_order_collision` | Two public projects share a showcase order. | `tests/test_validation.py::test_showcase_order_collision_applies_only_to_public_projects` |
| `credential_material` | Curated text matches a credential pattern. | `tests/test_validation.py::test_credential_material_is_an_error` |

## Sweep result

- Initial name-and-fixture pass found four uncovered schema rules.
- The first assertion-level pass found accomplishment-link, MCP filter, and
  MCP-002 timestamp assertions that looked covered by names or fixture setup
  but were not actually asserted.
- After filling those gaps, two complete passes over this matrix found no
  remaining `GAP`, unsupported criterion, or stale pytest node id.
