'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useRouter } from 'next/navigation';

export default function PatientHome() {
  const router = useRouter();
  const [patient, setPatient] = useState(null);
  useEffect(() => {
    api('patients').then(rows => setPatient(rows[0]));
  }, []);
  async function start() {
    const session = await api('sessions', { patient_id: patient.id, exercise_id: 'shoulder_abduction', consent_recording: true });
    router.push('/live/' + session.id);
  }
  if (!patient) return <p className="p-8">Loading…</p>;
  return (
    <main className="p-10 max-w-3xl">
      <p className="uppercase tracking-widest text-sm">Today</p>
      <h1 className="font-serif text-5xl mt-2">Start when you are ready.</h1>
      <p className="mt-4 mb-8">Your physiotherapist remains responsible for the plan. This session records movement, not a diagnosis.</p>
      <button className="w-full bg-pine text-lime font-serif text-3xl py-6 rounded-2xl" onClick={start}>Start session</button>
    </main>
  );
}
