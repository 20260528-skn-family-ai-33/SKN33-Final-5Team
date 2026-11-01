"""Import Deep Agents only when an agent is actually built.

The package loads provider SDKs this project does not use (Anthropic alone takes about
1.4 s), and every worker job paid for it even when no model was called.
"""


def create_deep_agent(*args, **kwargs):
    from deepagents import create_deep_agent as build
    return build(*args, **kwargs)
