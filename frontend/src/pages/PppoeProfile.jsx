import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import toast from 'react-hot-toast';

const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api' });

export default function PppoeProfile() {
  const { tenantId } = useParams();
  const expired = window.location.pathname.startsWith('/pppoe-expired/');
  const [profile, setProfile] = useState(null);
  const [packages, setPackages] = useState([]);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const username = new URLSearchParams(window.location.search).get('username') || '';
    Promise.all([api.get(`/public/${tenantId}/pppoe-profile?username=${encodeURIComponent(username)}`), api.get(`/public/${tenantId}/packages?service_type=pppoe`)]).then(([p, pkgs]) => { setProfile(p.data); setPackages(pkgs.data || []); }).catch((e) => toast.error(e.response?.data?.message || 'Unable to load profile')).finally(() => setLoading(false));
  }, [tenantId]);
  const pay = async () => {
    const pkg = packages.find((item) => item.id === selected);
    if (!pkg) return toast.error('Select a PPPoE package');
    try { const { data } = await api.post(`/public/${tenantId}/pay`, { package_id: pkg.id, phone: profile.customer.phone, service_type: 'pppoe', username: profile.customer.username }); if (data.authorizationUrl) window.location.href = data.authorizationUrl; else toast.error('Payment checkout was not returned'); } catch (e) { toast.error(e.response?.data?.message || 'Could not start payment'); }
  };
  if (loading) return <main className="flex min-h-screen items-center justify-center bg-slate-100">Loading profile...</main>;
  if (!profile) return <main className="flex min-h-screen items-center justify-center bg-slate-100">Profile unavailable.</main>;
  const c = profile.customer;
  return <main className="min-h-screen bg-slate-100 px-4 py-8"><section className="mx-auto max-w-3xl rounded-xl bg-white p-6 shadow ring-1 ring-slate-200"><p className="text-xs font-bold uppercase tracking-widest text-blue-600">{profile.tenant.business_name}</p><h1 className="mt-2 text-2xl font-bold text-slate-900">{expired ? 'PPPoE subscription expired' : 'My PPPoE profile'}</h1><p className="mt-2 text-sm text-slate-500">Manage your package, renew early, and view your internet usage.</p><div className="mt-6 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-slate-50 p-4"><p className="text-xs text-slate-500">Username</p><p className="font-bold text-slate-900">{c.username}</p><p className="mt-3 text-xs text-slate-500">Current package</p><p className="font-semibold text-slate-900">{c.package || '-'}</p></div><div className="rounded-lg bg-slate-50 p-4"><p className="text-xs text-slate-500">Expires</p><p className="font-bold text-slate-900">{c.expiry_date ? new Date(c.expiry_date).toLocaleString() : '-'}</p><p className="mt-3 text-xs text-slate-500">Usage</p><p className="font-semibold text-slate-900">{profile.usage.megabytes} MB · {profile.usage.active_sessions} active sessions</p></div></div><div className="mt-6 border-t pt-5"><label className="block text-sm font-semibold text-slate-900">Choose a PPPoE package</label><select className="form-input mt-2" value={selected} onChange={(e) => setSelected(e.target.value)}><option value="">Select package</option>{packages.map((pkg) => <option key={pkg.id} value={pkg.id}>{pkg.name} — Ksh {pkg.price}</option>)}</select><button type="button" className="btn-primary mt-4 w-full" onClick={pay}>Pay / Renew package</button></div></section></main>;
}
