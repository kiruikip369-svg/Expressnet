import { Download, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import adminApi from '../../api/adminAxios';

function formatKES(value) {
  return `KES ${Number(value || 0).toLocaleString()}`;
}

function statusTone(item) {
  if (item.status === 'expired' || Number(item.days_until_expiry) < 0) return 'bg-red-100 text-red-700';
  if (Number(item.days_until_expiry) <= 7) return 'bg-amber-100 text-amber-700';
  return 'bg-emerald-100 text-emerald-700';
}

export default function AdminSubscriptions() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('all');
  const [plan, setPlan] = useState('all');
  const [selected, setSelected] = useState([]);
  const [paymentTarget, setPaymentTarget] = useState(null);
  const [payment, setPayment] = useState({ amount: '', method: 'manual', reference: '', notes: '' });

  async function load() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page_size: '200' });
      if (status !== 'all') params.set('status', status);
      if (plan !== 'all') params.set('plan', plan);
      const { data } = await adminApi.get(`/admin/subscriptions?${params.toString()}`);
      setRows(data.results || []);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to load subscriptions');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [status, plan]);

  const summary = useMemo(() => ({
    active: rows.filter((row) => row.status !== 'expired').length,
    expiring: rows.filter((row) => Number(row.days_until_expiry) >= 0 && Number(row.days_until_expiry) <= 7).length,
    expired: rows.filter((row) => row.status === 'expired' || Number(row.days_until_expiry) < 0).length,
    mrr: rows.reduce((sum, row) => sum + Number(row.amount || 0), 0),
  }), [rows]);

  const exportCsv = () => {
    const headers = ['tenant_name', 'plan', 'amount', 'expires_at', 'days_until_expiry', 'last_paid_at', 'status'];
    const csv = [headers.join(','), ...rows.map((row) => headers.map((key) => JSON.stringify(row[key] ?? '')).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'subscriptions.csv';
    link.click();
    URL.revokeObjectURL(url);
  };

  const extendSelected = async () => {
    await Promise.all(selected.map((id) => {
      const row = rows.find((item) => item.id === id);
      const date = row?.expires_at ? new Date(row.expires_at) : new Date();
      date.setDate(date.getDate() + 30);
      return adminApi.patch(`/admin/subscriptions/${id}`, { expires_at: date.toISOString() });
    }));
    toast.success('Selected subscriptions extended');
    setSelected([]);
    load();
  };

  const recordPayment = async (event) => {
    event.preventDefault();
    try {
      await adminApi.post(`/admin/subscriptions/${paymentTarget.id}/payments`, payment);
      toast.success('Payment recorded');
      setPaymentTarget(null);
      setPayment({ amount: '', method: 'manual', reference: '', notes: '' });
      load();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to record payment');
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Subscriptions</h1>
          <p className="mt-1 text-xs text-slate-500">Manage monthly platform billing for tenant ISPs.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={exportCsv}><Download size={15} />Export CSV</button>
          <button type="button" className="btn-secondary" disabled={!selected.length} onClick={extendSelected}><RefreshCw size={15} />Extend selected</button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        {[
          ['Active', summary.active],
          ['Expiring This Week', summary.expiring],
          ['Expired', summary.expired],
          ['Monthly MRR', formatKES(summary.mrr)],
        ].map(([label, value]) => <div key={label} className="rounded-lg bg-white p-3 shadow-soft ring-1 ring-slate-200"><p className="text-xs font-semibold text-slate-500">{label}</p><p className="mt-1 text-xl font-bold text-slate-900">{value}</p></div>)}
      </div>

      <div className="flex flex-wrap gap-2">
        {['all', 'active', 'expiring_soon', 'expired'].map((item) => <button key={item} className={`rounded-md px-3 py-2 text-xs font-bold ${status === item ? 'bg-[#e94560] text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200'}`} onClick={() => setStatus(item)}>{item.replace('_', ' ')}</button>)}
        {['all', 'basic', 'pro', 'enterprise'].map((item) => <button key={item} className={`rounded-md px-3 py-2 text-xs font-bold ${plan === item ? 'bg-[#16213e] text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200'}`} onClick={() => setPlan(item)}>{item}</button>)}
      </div>

      <div className="table-shell overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="table-head"><tr><th className="px-4 py-3"></th><th className="px-4 py-3">Business</th><th className="px-4 py-3">Plan</th><th className="px-4 py-3">Amount/mo</th><th className="px-4 py-3">Expires</th><th className="px-4 py-3">Days Left</th><th className="px-4 py-3">Last Paid</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Actions</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? <tr><td className="table-cell" colSpan="9">Loading subscriptions...</td></tr> : rows.length === 0 ? <tr><td className="table-cell" colSpan="9">No subscriptions found.</td></tr> : rows.map((row) => (
              <tr key={row.id}>
                <td className="table-cell"><input type="checkbox" checked={selected.includes(row.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, row.id] : current.filter((id) => id !== row.id))} /></td>
                <td className="table-cell font-semibold text-slate-900">{row.tenant_name}</td>
                <td className="table-cell"><span className="rounded-full bg-blue-100 px-2 py-1 text-xs font-bold text-blue-700">{row.plan}</span></td>
                <td className="table-cell">{formatKES(row.amount)}</td>
                <td className="table-cell">{row.expires_at ? new Date(row.expires_at).toLocaleDateString() : '-'}</td>
                <td className="table-cell">{row.days_until_expiry ?? '-'}</td>
                <td className="table-cell">{row.last_paid_at ? new Date(row.last_paid_at).toLocaleDateString() : '-'}</td>
                <td className="table-cell"><span className={`rounded-full px-2 py-1 text-xs font-bold ${statusTone(row)}`}>{row.status}</span></td>
                <td className="table-cell"><button className="btn-secondary" onClick={() => { setPaymentTarget(row); setPayment((current) => ({ ...current, amount: row.amount })); }}><Plus size={15} />Record Payment</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {paymentTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <form className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl" onSubmit={recordPayment}>
            <h2 className="text-lg font-bold text-slate-900">Record Payment - {paymentTarget.tenant_name}</h2>
            <div className="mt-4 space-y-3">
              <input className="form-input" type="number" value={payment.amount} onChange={(e) => setPayment((c) => ({ ...c, amount: e.target.value }))} placeholder="Amount" />
              <select className="form-input" value={payment.method} onChange={(e) => setPayment((c) => ({ ...c, method: e.target.value }))}><option>manual</option><option>mpesa</option></select>
              <input className="form-input" value={payment.reference} onChange={(e) => setPayment((c) => ({ ...c, reference: e.target.value }))} placeholder="Reference" />
              <textarea className="form-input min-h-20" value={payment.notes} onChange={(e) => setPayment((c) => ({ ...c, notes: e.target.value }))} placeholder="Notes" />
            </div>
            <div className="mt-4 flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setPaymentTarget(null)}>Cancel</button><button className="rounded-md bg-[#e94560] px-4 py-2 text-xs font-bold text-white">Save</button></div>
          </form>
        </div>
      )}
    </div>
  );
}
