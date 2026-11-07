import unittest
import assessment_runner

class AssessmentTests(unittest.TestCase):
    def test_parse_har_confirmed_question_shape(self):
        payload = [{"question": {"id": 90004010, "type": "choice", "content": "Q"},
                    "choices": [{"id": 900040101, "content": "console.log(1);"}],
                    "descriptions": None}]
        q = assessment_runner.parse_questions(payload)
        self.assertEqual(q[0]["id"], "90004010")
        self.assertEqual(q[0]["choices"][0]["id"], 900040101)

    def test_choice_requires_exact_id_or_text(self):
        q = {"id": "1", "type": "choice", "choices": [{"id": 11, "content": "A"}]}
        row = {"suggested_answer": "A", "matched_choice_id": 11}
        ans, reason = assessment_runner.pick_answer(q, row)
        self.assertEqual(ans["choice_id"], 11)
        self.assertEqual(reason, "live_choice_text_match")


    def test_choice_mapping_uses_live_text_not_stored_id_or_position(self):
        # The option moved and the server assigned different IDs this attempt.
        q = {"id": "1", "type": "choice", "choices": [
            {"id": 903, "content": "console.log(2);"},
            {"id": 901, "content": "console.log(1);"},
            {"id": 902, "content": "console.log(3);"},
        ]}
        row = {"suggested_answer": "console.log(1);", "matched_choice": "console.log(1);",
               "matched_choice_id": 101}
        ans, reason = assessment_runner.pick_answer(q, row)
        self.assertEqual(ans["choice_id"], 901)
        self.assertEqual(reason, "live_choice_text_match")

    def test_choice_mapping_preserves_operators(self):
        q = {"id": "2", "type": "choice", "choices": [
            {"id": 201, "content": "score >= 60"},
            {"id": 202, "content": "score <= 60"},
        ]}
        row = {"suggested_answer": "score >= 60", "matched_choice": "score >= 60",
               "matched_choice_id": 202}
        ans, _ = assessment_runner.pick_answer(q, row)
        self.assertEqual(ans["choice_id"], 201)

    def test_unknown_choice_is_not_guessed(self):
        q = {"id": "1", "type": "choice", "choices": [{"id": 11, "content": "A"}]}
        row = {"suggested_answer": "B", "matched_choice_id": 99}
        ans, reason = assessment_runner.pick_answer(q, row)
        self.assertIsNone(ans)
        self.assertEqual(reason, "choice_not_mapped_uniquely_by_live_text")


    def test_extracts_missing_expression_from_noisy_answer(self):
        row = {"suggested_answer": "الإجابة نكتب في النواقص\n\nscore >= 60\n\nشكل الكود النهائي\nlet score = 80;"}
        self.assertEqual(assessment_runner.extract_description_answer(row), "score >= 60")

    def test_rejects_long_unstructured_description_answer(self):
        row = {"suggested_answer": "محتوى ترويجي طويل " * 500}
        self.assertIsNone(assessment_runner.extract_description_answer(row))

    def test_description_answer_uses_compact_extracted_code(self):
        q = {"id": "90006050", "type": "description", "choices": []}
        row = {"suggested_answer": "نكتب في النواقص\nscore >= 60\nشكل الكود النهائي\nlet score = 80;"}
        ans, reason = assessment_runner.pick_answer(q, row)
        self.assertEqual(ans["answer_code"], "score >= 60")
        self.assertEqual(reason, "exact_question_id")

    def test_extracts_else_if_insert_line_without_page_prose(self):
        row = {"suggested_answer": 'الاجابه المطلوب اضافه هذا السطر الثالث\nelse if (num == 2){console.log("Two")};\n\nليصبح الكود النهائي'}
        self.assertEqual(assessment_runner.extract_description_answer(row),
                         'else if (num == 2){console.log("Two")}')

    def test_live_description_templates_for_levels_1_to_7(self):
        samples = [
            ("90001020", 'اعرض "Good morning" في وحدة التحكم.', "console.log", 'console.log("Good morning");'),
            ("90002030", "احسب 19+40 واعرض نتيجة الحساب في وحدة التحكم.", "console.log();", "console.log(19+40);"),
            ("90003030", "أعلن المتغيّر number", "", "let number;"),
            ("90004020", 'إخراج "Hello" على الـ console', "", 'console.log("Hello");'),
            ("90005010", "num القيمة 5 ويكتب البرنامج في سطرين", "", "let num = 5;\nconsole.log(num);"),
            ("90005040", "num يساوي 100 اعرض Correct", "", 'let num = 100;\nif (num == 100){console.log("Correct");}'),
            ("90006020", 'num يساوي 10 أخرج "Win" وإلا "Lose"', "", 'let num = 5;\nif (num == 10){console.log("Win");}\nelse {console.log("Lose");}'),
            ("90007020", 'في السطر الثالث num هي 2 اعرض "Two"', 'let num = 2;\nif (num == 1){console.log("One");}', None),
        ]
        for qid, content, starter, expected in samples:
            q = {"id": qid, "type": "description", "content": content,
                 "descriptions": [{"code": starter}]}
            row = {"suggested_answer": "reference text"}
            ans, reason = assessment_runner.pick_answer(q, row)
            self.assertIsNotNone(ans, qid)
            self.assertEqual(reason, "live_prompt_and_starter_code_template")
            if qid == "90007020":
                self.assertTrue(ans["answer_code"].endswith('else if (num == 2){console.log("Two");}'))
            else:
                self.assertEqual(ans["answer_code"], expected, qid)

    def test_reads_server_correctness_and_completion(self):
        payload = {"result": {"correct": False, "model_answer": "MODEL"},
                   "complete": {"chapter": {"completed_at": "2026-11-03T00:00:00Z"}}}
        self.assertEqual(assessment_runner.response_completion(payload),
                         (False, "MODEL", True))

    def test_extracts_level8_single_blank_wording(self):
        row = {"suggested_answer": "الاجابه نكتب في المكان الفارغ\n\nscore >= 60\n\nليصبح الكود هكذا\nlet score = 70;"}
        self.assertEqual(assessment_runner.extract_description_answer(row), "score >= 60")

    def test_extracts_ordered_level8_fill_blanks_without_page_text(self):
        row = {"suggested_answer": "نكتب النواقص في الاسطر هكذا بالترتيب\n\nnum <= 3\nnum >= 6\nelse\n\nليصبح شكل الكود النهائي\nlet num = 4;"}
        self.assertEqual(assessment_runner.extract_description_answer(row), "num <= 3\nnum >= 6\nelse")

    def test_retake_does_not_skip_answers_from_previous_attempt(self):
        answered_ids = ["90001010", "90001020"]
        # Retake must resend the first ordered question even if the old ledger
        # contains it; normal resume still skips already accepted prior answers.
        self.assertFalse(assessment_runner.should_skip_prior_answer("90001010", answered_ids, retake=True))
        self.assertTrue(assessment_runner.should_skip_prior_answer("90001010", answered_ids, retake=False))


if __name__ == "__main__":
    unittest.main()
