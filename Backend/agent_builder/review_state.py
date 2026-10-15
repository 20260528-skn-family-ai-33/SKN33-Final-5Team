"""Collect one approval item per review while preserving the original AI report."""


def collect_reviews(outputs):
    reviews = {}
    for output in outputs.values():
        for port in ('reviews', '_reviews', '_review_updates'):
            for review in output.get(port, []):
                previous = reviews.get(review['id'])
                # A later pass-through of the original report must not undo a rewrite.
                if previous is None or review.get('candidate_revision', 0) >= previous.get('candidate_revision', 0):
                    reviews[review['id']] = review
    return list(reviews.values())


def candidate_reply(review):
    return review.get('candidate_reply', review['report']['revised_reply'])


def is_review_report_artifact(config, outputs, artifact):
    if artifact.get('review_report') is True:
        return True
    # Older checkpoints predate the artifact marker. Trust a registered writer
    # only when its actual bound input identifies an unapproved diagnostic table.
    for node in config['workflow']['nodes']:
        if node['tool'] != 'write_result' or outputs.get(node['id'], {}).get('artifact') != artifact:
            continue
        binding = next((b for b in node['bindings'] if b['input'] == 'table'), None)
        if binding is None:
            continue
        table = outputs.get(binding['node_id'], {}).get(binding['output'], {})
        format = node['settings'].get('format') or config['execution_plan']['output_format']
        if table.get('review_report') is True and artifact.get('name') == '검수_보고서.' + format:
            return True
    return False
