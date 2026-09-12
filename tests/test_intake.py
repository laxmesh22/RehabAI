import unittest
from backend.intake import apply_confirmed_value, merge_finish_intake, missing_intake_fields, parse_utterance


class IntakeParseTests(unittest.TestCase):
    def test_english_pain_number_and_words(self):
        four = parse_utterance('four out of ten', 'pain_rest')
        self.assertEqual(four['intent'], 'number')
        self.assertEqual(four['parsed_value'], 4)
        self.assertTrue(four['needs_confirm'])
        self.assertEqual(parse_utterance('7', 'pain_movement')['parsed_value'], 7)

    def test_hindi_pain_word(self):
        row = parse_utterance('चार', 'pain_rest')
        self.assertEqual(row['parsed_value'], 4)
        self.assertEqual(row['intent'], 'number')

    def test_function_labels_and_scale(self):
        self.assertEqual(parse_utterance('no difficulty', 'difficulty_dressing')['parsed_value'], 0)
        self.assertEqual(parse_utterance('mild', 'difficulty_grooming')['parsed_value'], 1)
        self.assertEqual(parse_utterance('unable', 'difficulty_overhead')['parsed_value'], 4)
        self.assertEqual(parse_utterance('2', 'difficulty_behind_back')['parsed_value'], 2)

    def test_confirm_yes_no_and_safety(self):
        yes = parse_utterance('yes', 'pain_rest', awaiting_confirm=True)
        self.assertEqual(yes['intent'], 'confirm_yes')
        no = parse_utterance('no', 'pain_rest', awaiting_confirm=True)
        self.assertEqual(no['intent'], 'confirm_no')
        stop = parse_utterance('stop, it hurts a lot', 'pain_movement')
        self.assertEqual(stop['intent'], 'safety_pause')
        self.assertEqual(stop['safety'], 'PAUSE')
        numb = parse_utterance('I have numbness in the fingers', 'pain_movement')
        self.assertEqual(numb['intent'], 'safety_pause')
        number = parse_utterance('what number should I say', 'pain_rest')
        self.assertNotEqual(number['intent'], 'safety_pause')

    def test_does_not_invent_a_number(self):
        row = parse_utterance('my shoulder feels frozen', 'pain_rest')
        self.assertEqual(row['intent'], 'unknown')
        self.assertIsNone(row['parsed_value'])

    def test_confirmed_intake_is_complete_only_with_all_fields(self):
        intake = None
        intake = apply_confirmed_value(intake, 'pain_rest', 3, 'voice', 'three')
        self.assertFalse(intake['confirmed'])
        values = {
            'pain_movement': 6,
            'difficulty_dressing': 2,
            'difficulty_grooming': 1,
            'difficulty_overhead': 3,
            'difficulty_behind_back': 4,
        }
        for field, value in values.items():
            intake = apply_confirmed_value(intake, field, value, 'tap')
        self.assertTrue(intake['confirmed'])
        self.assertEqual(intake['source'], 'mixed')
        self.assertEqual(missing_intake_fields(intake), [])

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            apply_confirmed_value(None, 'pain_rest', 11, 'tap')

    def test_finish_merge_prefers_body_over_stored(self):
        class Body:
            pain_rest = 1
            pain_movement = None
            difficulty_dressing = None
            difficulty_grooming = None
            difficulty_overhead = None
            difficulty_behind_back = None

        merged = merge_finish_intake({'pain_rest': 4, 'pain_movement': 7}, Body())
        self.assertEqual(merged['pain_rest'], 1)
        self.assertEqual(merged['pain_movement'], 7)
