'use client';
import { useEffect, useState } from 'react';
import { api } from '../../../lib/api';
import { useParams, useRouter } from 'next/navigation';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

export default function PatientPage() {
  const { id } = useParams();
  const router = useRouter();
  const [patient, setPatient] = useState(null);
  const [rom, setRom] = useState([]);
  const [question, setQuestion] = useState('How has this patient improved?');
  const [answer, setAnswer] = useState('');
  useEffect(() => {
    api('patients/' + id).then(setPatient);
    api('patients/' + id + '/rom').then(setRom);
  }, [id]);
  async function start() {
    const session = await api('sessions', { patient_id: id, exercise_id: 'shoulder_abduction', consent_recording: true, goal: 5, target: 80 });
    router.push('/live/' + session.id);
  }
  async function ask() {
    const res = await api('agent/query', { patient_id: id, question });
    setAnswer(res.summary);
  }
  if (!patient) return <p className="p-8">Loading…</p>;
  const abd = rom.filter(r => r.movement === 'abduction').map((r, i) => ({ i, value: r.value }));
  const p = patient.progress || {};
  return (
    <main className="p-8 space-y-6">
      <p className="uppercase text-sm">{patient.is_demo ? 'Demo patient' : 'Patient'} · {patient.mrn}</p>
      <h1 className="font-serif text-4xl">{patient.full_name}</h1>
      <p>{patient.clinician_diagnosis}</p>
      <div className="grid md:grid-cols-3 gap-4">
        <div className="bg-white p-4 rounded-2xl border">Abduction {p.abduction?.current ?? '—'}° ({p.abduction ? (p.abduction.change > 0 ? '+' : '') + p.abduction.change : ''}°)</div>
        <div className="bg-white p-4 rounded-2xl border">Flexion {p.flexion?.current ?? '—'}°</div>
        <div className="bg-white p-4 rounded-2xl border">Pain {p.pain_movement?.current ?? '—'}/10</div>
      </div>
      <button className="bg-pine text-lime px-4 py-3 rounded-xl" onClick={start}>Start assessment</button>
      <div className="h-64 bg-white rounded-2xl p-4 border">
        <ResponsiveContainer>
          <LineChart data={abd}><XAxis dataKey="i" /><YAxis /><Tooltip /><Line type="monotone" dataKey="value" stroke="#16362e" /></LineChart>
        </ResponsiveContainer>
      </div>
      <div className="bg-white p-4 rounded-2xl border space-y-3">
        <h2 className="font-serif text-2xl">AI assistant</h2>
        <textarea className="w-full border rounded-xl p-2" value={question} onChange={e => setQuestion(e.target.value)} />
        <button className="bg-pine text-lime px-3 py-2 rounded-xl" onClick={ask}>Ask</button>
        <p>{answer}</p>
      </div>
    </main>
  );
}
