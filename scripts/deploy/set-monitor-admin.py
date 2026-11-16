#!/usr/bin/env python3
"""Explicit, one-account administrator assignment; never an email-based login allowlist."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    parser.add_argument('--apply', action='store_true', help='promote the uniquely matched active Cognito member')
    args = parser.parse_args()
    import psycopg
    from infrastructure.persistence.db import postgres_conninfo
    email = args.email.strip().lower()
    with psycopg.connect(**postgres_conninfo()) as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        rows = connection.execute("SELECT id,email,role,status,auth_provider FROM a4b.users WHERE email=%s FOR UPDATE", (email,)).fetchall()
        if len(rows) != 1 or rows[0][3] != 'active' or not rows[0][4].startswith('cognito:'):
            raise ValueError('Exactly one active Cognito member must exist; sign up and verify the email first.')
        member, address, old_role, status, provider = rows[0]
        if args.apply:
            connection.execute("UPDATE a4b.users SET role='admin' WHERE id=%s AND status='active'", (member,))
        print(json.dumps({'member_id': str(member), 'email': address, 'previous_role': old_role,
                          'role': 'admin' if args.apply else old_role, 'applied': args.apply}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        sys.exit('Administrator assignment failed: ' + type(error).__name__)
