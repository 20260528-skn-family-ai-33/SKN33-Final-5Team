"""Run supplied parser regressions against integrated runtime modules."""
import importlib.util,json,sys,unittest,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
runtime=root/'Backend/scripts';reference=root/'Backend/integrations/parser_handoff/scripts'
sys.path.insert(0,str(runtime))
suite=unittest.TestSuite()
for path in sorted(reference.glob('test_*.py')):
 spec=importlib.util.spec_from_file_location('handoff_'+path.stem,path)
 module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
 suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result=unittest.TextTestRunner(verbosity=1).run(suite)
import parser_converter
report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
 'runtime_module':str(Path(parser_converter.__file__).relative_to(root)),
 'source_matches_handoff':{p.name:(runtime/p.name).read_bytes()==p.read_bytes() for p in reference.glob('*.py') if not p.name.startswith('test_')}}
(root/'output/parser-runtime-regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
raise SystemExit(0 if result.wasSuccessful() else 1)
