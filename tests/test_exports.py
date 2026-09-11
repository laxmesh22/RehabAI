import io
import unittest
import zipfile
import xml.etree.ElementTree as ET

from backend.exports import patient_record_json, patient_record_xlsx


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            'schema_version': 'rehabai.patient-export.v1',
            'exported_at': '2026-09-11T10:00:00+00:00',
            'clinical_notice': 'Not a diagnosis.',
            'patient': {
                'id': 'PTEST', 'mrn': 'MRN-TEST', 'full_name': 'Test Patient',
                'date_of_birth': '1990-01-01', 'sex': 'Female', 'affected_side': 'right',
                'clinician_diagnosis': None, 'is_demo': True,
            },
            'questionnaire_sessions': [{
                'session_id': 'S1', 'started_at': '2026-09-11T10:00:00Z', 'language': 'en-IN',
                'confirmed': True, 'source': 'voice',
                'answers': {'pain_rest': 3, 'pain_movement': 6},
                'fields': {'pain_rest': {'value': 3, 'source': 'voice', 'transcript': 'three'}},
            }],
            'assessments': [], 'sessions': [], 'rom_measurements': [],
            'pain_scores': [], 'compensation_events': [],
        }

    def test_json_is_utf8_structured_record(self):
        data = patient_record_json(self.record)
        self.assertIn(b'rehabai.patient-export.v1', data)
        self.assertIn(b'questionnaire_sessions', data)

    def test_xlsx_is_valid_ooxml_package_with_expected_sheets(self):
        data = patient_record_xlsx(self.record)
        self.assertTrue(data.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            bad = archive.testzip()
            self.assertIsNone(bad)
            names = set(archive.namelist())
            self.assertIn('xl/workbook.xml', names)
            self.assertIn('xl/worksheets/sheet8.xml', names)
            for name in names:
                if name.endswith('.xml'):
                    ET.fromstring(archive.read(name))
            workbook = archive.read('xl/workbook.xml').decode('utf-8')
            self.assertIn('Questionnaire', workbook)
            self.assertIn('Data Dictionary', workbook)


if __name__ == '__main__':
    unittest.main()
