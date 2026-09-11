'use client';
import { useEffect, useRef, useState } from 'react';
import { api, token } from '../../../lib/api';
import { useParams, useRouter } from 'next/navigation';

export default function LivePage() {
  const { id } = useParams();
  const router = useRouter();
  const canvasRef = useRef(null);
  const [row, setRow] = useState(null);
  const [session, setSession] = useState(null);
  useEffect(() => {
    api('sessions/' + id).then(setSession);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.hostname}:8000/api/ws/sessions/${id}?token=${encodeURIComponent(token())}`);
    ws.onmessage = ev => {
      const data = JSON.parse(ev.data);
      setRow(data);
      const canvas = canvasRef.current;
      if (!canvas || !data.overlay) return;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#10241f';
      ctx.fillRect(0, 0, 640, 480);
      for (const bone of data.overlay.bones || []) {
        const a = data.overlay.points[bone.a], b = data.overlay.points[bone.b];
        if (!a || !b) continue;
        ctx.strokeStyle = bone.active ? '#d7f56a' : '#7f9b90';
        ctx.lineWidth = 6;
        ctx.beginPath();
        ctx.moveTo(a[0] * 640, a[1] * 480);
        ctx.lineTo(b[0] * 640, b[1] * 480);
        ctx.stroke();
      }
    };
    return () => ws.close();
  }, [id]);
  if (!session) return <p className="p-8">Connecting…</p>;
  return (
    <main className="p-6 grid md:grid-cols-2 gap-6">
      <div className="bg-pine rounded-2xl overflow-hidden">
        <p className="text-lime p-3 text-xs tracking-[.2em]">{session.source === 'live' ? 'LIVE RGB-D + ARM IMU' : 'SYNTHETIC RGB-D + ARM IMU · NOT LIVE SENSORS'}</p>
        <canvas ref={canvasRef} width="640" height="480" className="w-full" />
      </div>
      <div>
        <h1 className="font-serif text-3xl">{session.exercise_id}</h1>
        <p className="text-5xl font-serif my-4">{row?.valid ? Math.round(row.shoulder_angle) : '—'}°</p>
        <p>Rep {row?.rep || 0} / {session.goal}</p>
        <p>Lean {Math.round(row?.torso_lean || 0)}°</p>
        <p className="text-2xl font-serif my-4">{row?.feedback}</p>
        <button className="bg-pine text-lime px-3 py-2 rounded-xl mr-2" onClick={() => api('sessions/' + id + '/confirm', {})}>Confirm tracking</button>
        <button className="border px-3 py-2 rounded-xl mr-2" onClick={() => api('sessions/' + id + '/fault', { fault: 'lean' })}>Simulate lean</button>
        <button className="border px-3 py-2 rounded-xl" onClick={async () => {
          await api('sessions/' + id + '/finish', { pain_after: 4, create_assessment: false });
          router.push('/patients/' + session.patient_id);
        }}>Stop & save</button>
      </div>
    </main>
  );
}
