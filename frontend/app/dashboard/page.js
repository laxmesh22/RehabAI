'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import Link from 'next/link';

export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api('dashboard').then(setData).catch(e => setError(e.message));
  }, []);
  if (error) return <p className="p-8">{error}</p>;
  if (!data) return <p className="p-8">Loading…</p>;
  return (
    <main className="p-8">
      <p className="uppercase tracking-widest text-sm">Clinician</p>
      <h1 className="font-serif text-4xl mb-6">Shoulder studio</h1>
      <div className="grid md:grid-cols-5 gap-4">
        {[['Patients today', data.today_patients], ['Assessments', data.assessments_today],
          ['Sessions', data.sessions_completed_today], ['Review', data.patients_requiring_review],
          ['Adherence', data.average_adherence == null ? '—' : data.average_adherence + '%']].map(([k, v]) => (
          <div key={k} className="bg-white rounded-2xl p-4 border"><p className="text-sm">{k}</p><p className="font-serif text-3xl">{v}</p></div>
        ))}
      </div>
      <h2 className="mt-8 font-serif text-2xl">Patients</h2>
      {(data.recent_patients || []).map(p => (
        <p key={p.id}><Link className="underline" href={'/patients/' + p.id}>{p.full_name}</Link> {p.is_demo ? '(demo)' : ''}</p>
      ))}
    </main>
  );
}
