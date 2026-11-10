"""The current server policy takes precedence over persisted reviewer flags."""
import os


def verifier_mode(mode=None):
    setting = mode or os.getenv('A4B_VERIFIER_MODE', 'off')
    if setting not in ('off', 'shadow', 'defer', 'enforce'):
        raise ValueError('A4B_VERIFIER_MODE는 off/shadow/defer/enforce이어야 합니다.')
    return setting


def disabled_review_update(mode=None):
    # Keep historical verdicts, drafts and messages. Only disable the active gate.
    if verifier_mode(mode) == 'off':
        return dict(verification_route=None, verification_enforced=False,
                    verification_required=False)
    return {}
