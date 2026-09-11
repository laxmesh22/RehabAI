'use client';
import { useState } from 'react';
import { api } from '../lib/api';
import { useRouter } from 'next/navigation';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('priya.mehta@hospital.local');
  const [password, setPassword] = useState('rehabai-demo');
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    try {
      const res = await api('auth/login', { email, password });
      localStorage.setItem('rehabai_token', res.token);
      router.push(res.user.role === 'PATIENT' ? '/patient' : '/dashboard');
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="min-h-screen grid md:grid-cols-2">
      <section className="bg-pine text-paper p-12 flex flex-col justify-between">
        <div>
          <p className="uppercase tracking-[.2em] text-sm">RehabAI</p>
          <h1 className="font-serif text-5xl text-lime mt-4">Digital twin of shoulder rehab.</h1>
          <p className="mt-6 max-w-md text-lime/80">Measurements, guided exercise and a clinician-reviewed record. Not a diagnosis service.</p>
        </div>
        <p>Jetson · RealSense · FastAPI · supervisor agent</p>
      </section>
      <form onSubmit={submit} className="p-12 flex flex-col justify-center gap-4">
        <h2 className="font-serif text-3xl">Sign in</h2>
        <input className="border rounded-xl p-3" value={email} onChange={e => setEmail(e.target.value)} />
        <input type="password" className="border rounded-xl p-3" value={password} onChange={e => setPassword(e.target.value)} />
        <button className="bg-pine text-lime rounded-xl p-3 font-semibold">Enter clinic</button>
        {error && <p className="text-red-700">{error}</p>}
        <p className="text-sm opacity-70">Demo password: rehabai-demo. Prefer the FastAPI studio at port 8000 for the live split-screen demo.</p>
      </form>
    </main>
  );
}
