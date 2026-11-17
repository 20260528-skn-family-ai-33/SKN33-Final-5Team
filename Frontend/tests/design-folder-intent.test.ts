import assert from 'node:assert/strict';
import test from 'node:test';
import {folderInputMode} from '../lib/design-folder-intent';

test('a receipt folder request selects files before design', () => {
  assert.equal(folderInputMode('자료함 영수증 폴더 확인해서 영수증 파일들 요약해줄래?'), 'all');
  assert.equal(folderInputMode('영수증 폴더의 모든 파일을 요약해줘'), 'all');
  assert.equal(folderInputMode('결과를 영수증 폴더에 저장해줘'), undefined);
});
