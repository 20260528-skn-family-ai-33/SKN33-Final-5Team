"""Missing-value rules shared by native row selection and design validation."""


UNARY_FILTER_OPERATORS = frozenset({'is_empty', 'not_empty'})


def comparison_operand_fields(rule):
    if rule['operator'] in UNARY_FILTER_OPERATORS:
        return ()
    if rule.get('compare_field'):
        return rule['field'], rule['compare_field']
    return (rule['field'],)


def missing_check_fields(filters, sort, policy, *, columns=None):
    """Return the checked columns, or None for all columns if no schema is supplied."""
    if policy == 'exclude' and not filters and not sort:
        return None if columns is None else set(columns)
    return ({rule['field'] for rule in filters if rule['operator'] not in UNARY_FILTER_OPERATORS}
            | {rule['compare_field'] for rule in filters if rule.get('compare_field')}
            | {item['field'] for item in sort})
