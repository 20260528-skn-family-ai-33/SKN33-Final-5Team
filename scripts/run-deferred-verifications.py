from pathlib import Path
from runpy import run_path

if __name__ == '__main__':
    run_path(str(Path(__file__).resolve().parent / 'run' / 'run-deferred-verifications.py'), run_name='__main__')
