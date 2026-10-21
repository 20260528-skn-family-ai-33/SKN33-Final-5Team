import json,unittest,io,time,sys,platform,importlib.metadata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
    started=time.perf_counter();buf=io.StringIO()
    result=unittest.TextTestRunner(stream=buf,verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'passed':result.wasSuccessful(),'seconds':time.perf_counter()-started,'python':platform.python_version(),
        'pydantic':importlib.metadata.version('pydantic'),'jsonschema':importlib.metadata.version('jsonschema'),'output':buf.getvalue()}
    (ROOT/'reports').mkdir(exist_ok=True);(ROOT/'reports/unit_tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(buf.getvalue());sys.exit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()
