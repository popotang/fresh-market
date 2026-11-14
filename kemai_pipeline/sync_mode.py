"""Pure --sync-mode contract shared by kemai_syncctl and the product CLI.

list (default): reuse the completed list snapshot and verify list-provided
fields only; never schedule object details.
on_demand: complete the selected list first, then queue bounded detail work for
explicitly requested fields/objects. Without an explicit detail demand it
executes as list and the plan must say so.
full: for accepted streams only, schedule the stream's supported details within
an explicit date/object scope and an explicit budget. There is no catalog-wide
default expansion, and paused detail capabilities stay blocked in every mode.

No I/O here: callers validate arguments, plans carry the verdicts, and the
executor re-checks local acceptance at run time (plans never grant permission).
"""

MODES = ('list', 'on_demand', 'full')


def resolve_mode(*, sync_mode=None, list_only=False, include_details=False,
                 required_fields=()):
    """Map legacy flags and explicit modes onto one contract; contradictions fail."""
    if sync_mode is not None and sync_mode not in MODES:
        raise ValueError('sync_mode must be one of ' + ','.join(MODES))
    fields = list(required_fields or ())
    if list_only and include_details:
        raise ValueError('Contradictory legacy flags: --list-only and --include-details')
    if sync_mode is None:
        if include_details:
            # Legacy scope only: the original purchase document-detail passes.
            mode, basis = 'full', 'legacy_include_details'
        elif list_only:
            mode, basis = 'list', 'legacy_list_only'
        elif fields:
            # Legacy behavior: required detail fields drove bounded lines/detail
            # execution before an explicit mode existed.
            mode, basis = 'on_demand', 'implicit_from_required_fields'
        else:
            mode, basis = 'list', 'default'
    else:
        mode, basis = sync_mode, 'explicit'
        if list_only and mode != 'list':
            raise ValueError('--list-only only maps to sync-mode list')
        if include_details and mode != 'full':
            raise ValueError('--include-details only maps to sync-mode full')
    legacy = [flag for flag, on in (('--list-only', list_only),
                                    ('--include-details', include_details)) if on]
    return {'sync_mode': mode, 'mode_basis': basis, 'legacy_flags': legacy}


def detail_demand(mode_info, groups, *, explicit_objects=False, detail_scope=None,
                  detail_budget=None, explicit_range=False):
    """Validate a mode against classified field groups.

    groups is field_catalog.classify() output. Unknown/unsupported/ambiguous
    fields and contradictory combinations raise ValueError (never guessed).
    Paused fields block the plan in every mode via {'detail_demand':'blocked'}.
    """
    mode = mode_info['sync_mode']
    needed, paused = groups['needs_detail'], groups['paused']
    if mode == 'list':
        if needed:
            raise ValueError('sync-mode list cannot require detail fields: ' + ','.join(sorted(needed)))
        demand = 'none'
    elif mode == 'on_demand':
        if not needed and not explicit_objects:
            demand = 'none'  # executes as list; the plan must state this
        else:
            demand = 'bounded'
    elif mode == 'full':
        if not explicit_range:
            raise ValueError('full requires an explicit date/object range; use a scoped history command')
        if not detail_scope:
            raise ValueError('full requires an explicit detail scope')
        if not isinstance(detail_budget, int) or detail_budget < 1:
            raise ValueError('full requires an explicit positive detail budget')
        demand = 'bounded'
    else:  # pragma: no cover - resolve_mode already validated
        raise ValueError('Invalid sync mode')
    result = {'detail_demand': demand, 'paused_fields': sorted(paused)}
    if needed:
        result['detail_fields'] = sorted(needed)
    if demand == 'bounded':
        result['detail_scope'] = detail_scope
        result['detail_budget'] = detail_budget if mode == 'full' else 'per-run budgets'
        if mode == 'full':
            # Planning-only until bounded detail tasks land (unit 3): the budget
            # is declared here and enforced by the executor that consumes it.
            result['detail_execution'] = ('planning_only_pending_unit3; '
                                          '--include-details keeps the legacy executable scope')
    if mode == 'on_demand' and demand == 'none':
        result['note'] = 'no explicit detail requirement; executes as list'
    if paused:
        result['detail_demand'] = 'blocked'
        result['reason'] = 'paused_by_user'
    return result


def coverage_annotations(mode_info, demand, *, list_scope, detail_coverage, detail_scope=None):
    """Standard plan-level coverage block: list and detail never collapse into one flag."""
    return {
        'sync_mode': mode_info['sync_mode'],
        'mode_basis': mode_info['mode_basis'],
        'list_coverage': {'scope': list_scope,
                          'evidence': 'per-capture day-window/list verification; '
                                      'final/provisional day-window semantics unchanged'},
        'detail_coverage': detail_coverage,
        'detail_scope': detail_scope,
        'detail_demand': demand['detail_demand'],
        'timing_semantics': {
            'source_observed_at': 'when the source list/detail was actually observed',
            'committed_at': 'when this data center wrote the rows; never source realtime'},
    }


def estimate_db_reads(item_count):
    """Worst-case plan-phase reads for an inventory selection: one products
    page per 100 items plus at most one profile page per 100 items (profile
    pages only occur when list evidence carries versions)."""
    if not isinstance(item_count, int) or isinstance(item_count, bool) or item_count < 0:
        raise ValueError('item_count must be a nonnegative integer')
    pages = (item_count + 99) // 100
    return {'products_reads': pages, 'profile_reads_max': pages, 'total_max': 2 * pages}


def db_budget(estimate, cap):
    """Fail-closed verdict for plan-phase DB reads, with the split strategy."""
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError('db budget must be a positive integer')
    over = estimate['total_max'] > cap
    return {'estimated_db_requests': estimate['total_max'], 'db_request_budget': cap,
            'over_budget': over,
            'strategy': 'within budget' if not over else
            'split into explicit-id plans of at most 100 objects each, or raise '
            'the budget explicitly; the plan issues no reads before this check'}
