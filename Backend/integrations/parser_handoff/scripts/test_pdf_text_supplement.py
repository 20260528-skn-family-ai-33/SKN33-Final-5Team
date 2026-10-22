import unittest
from pdf_text_supplement import select_supplements, normalized


class SupplementTests(unittest.TestCase):
    def test_smart_quotes_do_not_duplicate_cross_reference(self):
        self.assertEqual(select_supplements('"주메뉴 탭" p.37', '“주메뉴 탭” p.37', '“주메뉴 탭” p.37')[0], [])

    def test_whitespace_and_markdown_do_not_duplicate(self):
        text = '프린터 작동과 관련된 설정을 할 수 있습니다.'
        added, _ = select_supplements(text.replace(' ', '\n'), '**'+text+'**', text)
        self.assertEqual(added, [])

    def test_missing_sentence_added_once(self):
        old = '네트워크 연결 상태를 표시합니다.'
        missing = '프린터 작동과 관련된 설정을 할 수 있습니다.'
        markdown = old+'<br>'+missing+'\n'+missing
        added, _ = select_supplements(old, markdown, old+'\n'+missing)
        self.assertEqual(added, [missing])
        self.assertEqual(select_supplements(old+'\n'+missing, markdown, old+'\n'+missing)[0], [])

    def test_new_sentence_on_same_line_does_not_repeat_existing(self):
        old = '네트워크 연결 상태를 표시합니다.'
        missing = '프린터 작동과 관련된 설정을 할 수 있습니다.'
        self.assertEqual(select_supplements(old, old+' '+missing, old+' '+missing)[0], [missing])

    def test_conflicting_number_goes_to_review(self):
        old = '최대 5개 기간을 설정할 수 있습니다.'
        other = '최대 6개 기간을 설정할 수 있습니다.'
        added, review = select_supplements(old, other, other)
        self.assertEqual(added, [])
        self.assertEqual(len(review), 1)

    def test_partial_overlap_does_not_duplicate(self):
        old = '프린터 작동과 관련된 설정을'
        whole = old+' 할 수 있습니다.'
        self.assertEqual(select_supplements(old, whole, whole)[0], [])

    def test_candidate_must_exist_in_native_text(self):
        self.assertEqual(select_supplements('', '원본에 없는 추측된 문장입니다.', '실제 원본 내용입니다.')[0], [])

    def test_sign_and_decimal_are_distinct(self):
        self.assertNotEqual(normalized('1.5'), normalized('15'))
        self.assertNotEqual(normalized('-15'), normalized('15'))

    def test_identical_text_on_a_different_page_can_be_recovered(self):
        sentence='프린터 작동과 관련된 설정을 할 수 있습니다.'
        self.assertEqual(select_supplements('', sentence, sentence)[0], [sentence])


if __name__ == '__main__':
    unittest.main()
