from fastapi import HTTPException
from backend.database.models import Patient


def can_view_patient(user, patient: Patient):
    if patient is None:
        return False
    if user.hospital_id != patient.hospital_id:
        return False
    if user.role == 'ADMIN':
        return True
    if user.role == 'PATIENT':
        return patient.user_id == user.id
    if user.role == 'PHYSIOTHERAPIST':
        return patient.assigned_physio_id == user.id
    if user.role == 'DOCTOR':
        return patient.assigned_doctor_id == user.id
    return False


def can_treat(user, patient: Patient):
    return user.role in ('PHYSIOTHERAPIST', 'DOCTOR') and can_view_patient(user, patient)


def load_patient(db, user, patient_id):
    patient = db.get(Patient, patient_id)
    if patient is None or not can_view_patient(user, patient):
        raise HTTPException(404, 'Patient not found')
    return patient
