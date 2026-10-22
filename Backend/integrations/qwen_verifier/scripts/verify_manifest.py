"""압축 해제 후 전달 파일의 SHA-256을 대조한다. 모델 호출 없음."""
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    checked=0
    for line in (ROOT/'MANIFEST.sha256').read_text().splitlines():
        sha,relative=line.split('  ',1);path=(ROOT/relative).resolve()
        if not path.is_relative_to(ROOT.resolve()):raise ValueError('허용되지 않은 파일 경로입니다.')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('파일이 변경됐습니다: '+relative)
        checked+=1
    print(f'파일 {checked}개의 SHA-256이 일치합니다.')
if __name__=='__main__':main()
