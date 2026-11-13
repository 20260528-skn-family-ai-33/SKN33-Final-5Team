"""Bounded design-time paths, including the actual yes/no edges taken."""


def workflow_paths(workflow):
    by = {n['id']: n for n in workflow['nodes']}
    paths = []
    def visit(identifier, prefix, edges):
        if identifier not in by or identifier in {n['id'] for n in prefix} or len(paths) >= 256:
            raise ValueError('Invalid or excessive workflow paths')
        path = prefix + [by[identifier]]
        outgoing = [e for e in workflow['edges'] if e['source'] == identifier]
        if not outgoing or by[identifier]['tool'] == 'finish':
            paths.append((path, edges))
        else:
            for edge in outgoing:
                visit(edge['target'], path, edges + [edge])
    visit(workflow['entry'], [], [])
    return paths
