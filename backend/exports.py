"""Patient-scoped JSON and Excel export helpers.

The XLSX writer uses only the Python standard library so the hospital backend can
export on the Jetson/server without a desktop spreadsheet dependency.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from html import escape
from typing import Any, Iterable

from sqlalchemy import select

from backend.database.models import (
    Assessment, CompensationEvent, PainScore, Patient, ROMMeasurement, Session,
)


def build_patient_record(db, patient: Patient) -> dict[str, Any]:
    sessions = db.scalars(select(Session).where(Session.patient_id == patient.id).order_by(Session.started_at)).all()
    assessments = db.scalars(select(Assessment).where(Assessment.patient_id == patient.id).order_by(Assessment.created_at)).all()
    rom = db.scalars(select(ROMMeasurement).where(ROMMeasurement.patient_id == patient.id).order_by(ROMMeasurement.recorded_at)).all()
    pain = db.scalars(select(PainScore).where(PainScore.patient_id == patient.id).order_by(PainScore.recorded_at)).all()
    compensation = db.scalars(select(CompensationEvent).where(CompensationEvent.patient_id == patient.id).order_by(CompensationEvent.created_at)).all()
    return {
        'schema_version': 'rehabai.patient-export.v1',
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'clinical_notice': 'AI-assisted rehabilitation record. Measurements are not a diagnosis and require clinician review.',
        'patient': {
            'id': patient.id, 'mrn': patient.mrn, 'full_name': patient.full_name,
            'date_of_birth': patient.date_of_birth, 'sex': patient.sex,
            'affected_side': patient.affected_side,
            'clinician_diagnosis': patient.clinician_diagnosis,
            'is_demo': bool(patient.is_demo),
        },
        'questionnaire_sessions': [
            {
                'session_id': row.id,
                'started_at': _iso(row.started_at),
                'language': (row.intake or {}).get('language'),
                'confirmed': bool((row.intake or {}).get('confirmed')),
                'source': (row.intake or {}).get('source'),
                'answers': {key: (row.intake or {}).get(key) for key in (
                    'pain_rest', 'pain_movement', 'difficulty_dressing', 'difficulty_grooming',
                    'difficulty_overhead', 'difficulty_behind_back',
                )},
                'fields': (row.intake or {}).get('fields') or {},
            }
            for row in sessions if row.intake
        ],
        'assessments': [
            {
                'id': row.id, 'created_at': _iso(row.created_at), 'source': row.source,
                'model_version': row.model_version, 'affected_side': row.affected_side,
                'flexion_max_deg': row.flexion_max, 'abduction_max_deg': row.abduction_max,
                'pain_rest_0_10': row.pain_rest, 'pain_movement_0_10': row.pain_movement,
                'difficulty_dressing_0_4': row.difficulty_dressing,
                'difficulty_grooming_0_4': row.difficulty_grooming,
                'difficulty_overhead_0_4': row.difficulty_overhead,
                'difficulty_behind_back_0_4': row.difficulty_behind_back,
                'torso_compensation_deg': row.torso_compensation,
                'average_confidence': row.avg_confidence, 'smoothness': row.smoothness,
                'is_demo': bool(row.is_demo), 'notes': row.notes,
            } for row in assessments
        ],
        'sessions': [
            {
                'id': row.id, 'kind': row.kind, 'exercise_id': row.exercise_id,
                'side': row.side, 'started_at': _iso(row.started_at), 'ended_at': _iso(row.ended_at),
                'source': row.source, 'model_version': row.model_version, 'status': row.status,
                'goal': row.goal, 'valid_reps': row.reps, 'invalid_reps': row.invalid_reps,
                'peak_angle_deg': row.peak_angle, 'tracking_coverage_percent': row.coverage,
                'pain_before_0_10': row.pain_before, 'pain_after_0_10': row.pain_after,
                'safety_outcome': row.safety_outcome, 'is_demo': bool(row.is_demo),
            } for row in sessions
        ],
        'rom_measurements': [
            {
                'id': row.id, 'recorded_at': _iso(row.recorded_at), 'movement': row.movement,
                'value_deg': row.value, 'confidence': row.confidence, 'valid': bool(row.valid),
                'source': row.source, 'model_version': row.model_version,
                'assessment_id': row.assessment_id, 'session_id': row.session_id,
            } for row in rom
        ],
        'pain_scores': [
            {
                'id': row.id, 'recorded_at': _iso(row.recorded_at), 'rest_0_10': row.rest,
                'movement_0_10': row.movement, 'context': row.context,
                'assessment_id': row.assessment_id, 'session_id': row.session_id,
            } for row in pain
        ],
        'compensation_events': [
            {
                'id': row.id, 'created_at': _iso(row.created_at), 'type': row.type,
                'value_deg': row.value, 'threshold_deg': row.threshold,
                'assessment_id': row.assessment_id, 'session_id': row.session_id,
            } for row in compensation
        ],
    }


def patient_record_json(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')


def patient_record_xlsx(record: dict[str, Any]) -> bytes:
    patient = record['patient']
    sheets: list[tuple[str, list[list[Any]]]] = [
        ('Patient Summary', [
            ['RehabAI patient export', 'Value'],
            ['Clinical notice', record['clinical_notice']],
            ['Exported at', record['exported_at']],
            ['Patient ID', patient['id']], ['MRN', patient['mrn']], ['Full name', patient['full_name']],
            ['Date of birth', patient['date_of_birth']], ['Sex', patient['sex']],
            ['Affected side', patient['affected_side']], ['Clinician diagnosis', patient['clinician_diagnosis']],
            ['Demo / synthetic patient', patient['is_demo']],
        ]),
        ('Questionnaire', _questionnaire_rows(record['questionnaire_sessions'])),
        ('Assessments', _object_rows(record['assessments'])),
        ('Sessions', _object_rows(record['sessions'])),
        ('ROM', _object_rows(record['rom_measurements'])),
        ('Pain', _object_rows(record['pain_scores'])),
        ('Compensation', _object_rows(record['compensation_events'])),
        ('Data Dictionary', [
            ['Field', 'Meaning'],
            ['pain_*_0_10', 'Patient-reported score: 0 no pain, 10 worst pain'],
            ['difficulty_*_0_4', '0 none, 1 mild, 2 moderate, 3 severe, 4 unable'],
            ['source', 'simulation or live sensor/data source; do not treat simulation as hardware validation'],
            ['confidence', 'Model confidence; invalid low-confidence measurements are excluded from progress'],
            ['is_demo', 'True means seeded or simulated demonstration data'],
        ]),
    ]
    return _xlsx_bytes(sheets)


def _questionnaire_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    headers = ['session_id', 'started_at', 'language', 'confirmed', 'source', 'question_id', 'value', 'answer_source', 'transcript']
    rows = [headers]
    for item in items:
        fields = item.get('fields') or {}
        for question_id, value in (item.get('answers') or {}).items():
            detail = fields.get(question_id) or {}
            rows.append([
                item.get('session_id'), item.get('started_at'), item.get('language'), item.get('confirmed'),
                item.get('source'), question_id, value, detail.get('source'), detail.get('transcript'),
            ])
    return rows


def _object_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    if not items:
        return [['No records']]
    headers = list(items[0])
    return [headers] + [[item.get(key) for key in headers] for item in items]


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat() + ('Z' if value.tzinfo is None else '')


def _xlsx_bytes(sheets: Iterable[tuple[str, list[list[Any]]]]) -> bytes:
    sheet_list = list(sheets)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', _content_types(len(sheet_list)))
        archive.writestr('_rels/.rels', _root_rels())
        archive.writestr('xl/workbook.xml', _workbook(sheet_list))
        archive.writestr('xl/_rels/workbook.xml.rels', _workbook_rels(len(sheet_list)))
        archive.writestr('xl/styles.xml', _styles())
        for index, (_name, rows) in enumerate(sheet_list, start=1):
            archive.writestr(f'xl/worksheets/sheet{index}.xml', _sheet_xml(rows))
    return buffer.getvalue()


def _content_types(count: int) -> str:
    overrides = ''.join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, count + 1)
    )
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + overrides + '</Types>'


def _root_rels() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'


def _workbook(sheets: list[tuple[str, list[list[Any]]]]) -> str:
    tags = ''.join(f'<sheet name="{escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _rows) in enumerate(sheets, 1))
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + tags + '</sheets></workbook>'


def _workbook_rels(count: int) -> str:
    rels = ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1))
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + rels + f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'


def _styles() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F6B4F"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>'


def _sheet_xml(rows: list[list[Any]]) -> str:
    max_cols = max((len(row) for row in rows), default=1)
    widths = ''.join(f'<col min="{i}" max="{i}" width="{36 if i == 2 else 20}" customWidth="1"/>' for i in range(1, max_cols + 1))
    body = []
    for r_index, row in enumerate(rows, 1):
        cells = ''.join(_cell_xml(r_index, c_index, value, header=r_index == 1) for c_index, value in enumerate(row, 1))
        body.append(f'<row r="{r_index}">{cells}</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>' + widths + '</cols><sheetData>' + ''.join(body) + '</sheetData><autoFilter ref="A1:' + _column(max_cols) + str(max(1, len(rows))) + '"/></worksheet>'


def _cell_xml(row: int, col: int, value: Any, header: bool = False) -> str:
    ref = f'{_column(col)}{row}'
    style = ' s="1"' if header else ''
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = '' if value is None else str(value)
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _column(index: int) -> str:
    out = ''
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out
