import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from a4b_verifier.contracts import VerificationInput,VerificationOutput,VerificationResult
def main():
    out=ROOT/'schemas';out.mkdir(exist_ok=True)
    for name,model in [('input',VerificationInput),('output',VerificationOutput),('result',VerificationResult)]:
        schema={'$schema':'https://json-schema.org/draft/2020-12/schema',**model.model_json_schema()}
        (out/(name+'.schema.json')).write_text(json.dumps(schema,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
