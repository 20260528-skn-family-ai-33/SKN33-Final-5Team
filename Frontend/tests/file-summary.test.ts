import test from 'node:test';
import assert from 'node:assert/strict';
import {describeFile, type InspectedFile} from '../app/file-inspection';

const base: InspectedFile = {id: 'f', name: 'f', kind: 'document', has_readable_text: true, columns: [], fields: [], snippets: [], preview: []};

test('a table is described by the columns and rows actually read', () => {
  const file = {...base, name: '매출.csv', kind: 'table', columns: ['지역', '금액', '날짜', '담당', '비고']};
  assert.equal(describeFile(file, {row_count: 120}), '「매출.csv」 파일은 표 자료예요. 열 5개(지역, 금액, 날짜 등), 120행을 읽었어요.');
  assert.equal(describeFile(file), '「매출.csv」 파일은 표 자료예요. 열 5개(지역, 금액, 날짜 등)를 읽었어요.');
});

test('a workbook names its sheets', () => {
  const sheets = ['서울', '부산'].map(name => ({name, columns: ['주문번호', '금액'], preview: []}));
  const file = {...base, name: '지점.xlsx', kind: 'table', columns: ['주문번호', '금액'], tables: sheets};
  assert.match(describeFile(file), /시트 2개\(서울, 부산\)가 있어요\./);
});

test('a document reports its pages and the fields the parser found', () => {
  const file = {...base, name: '접수.pdf', fields: [{label: '접수번호', value: 'A-120'}, {label: '접수일'}]};
  assert.equal(describeFile(file, {page_count: 3}), '「접수.pdf」 파일은 문서 자료예요. 3쪽을 읽었어요. 찾은 항목: 접수번호, 접수일.');
});

test('an unreadable or partial file says so instead of guessing', () => {
  assert.match(describeFile({...base, name: '흐린 사진.jpg', has_readable_text: false}), /읽어 낸 글자가 없어요/);
  assert.match(describeFile({...base, name: '스캔.pdf', partial: true}), /일부 내용은 원본과 대조가 필요해요/);
});

test('the sentence never guesses a document type', () => {
  const text = describeFile({...base, name: '계약서.pdf', snippets: ['제1조 해지']}, {page_count: 12});
  assert.ok(!/로 보여요|계약서로|영수증/.test(text.replace('「계약서.pdf」', '')));
});
