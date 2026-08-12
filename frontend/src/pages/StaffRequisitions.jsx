import { Plus } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const blankForm = { type: 'tools', title: '', reason: '' };

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

function statusClass(status) {
  if (status === 'approved') return 'bg-blue-100 text-blue-700';
  if (status === 'issued') return 'bg-emerald-100 text-emerald-700';
  if (status === 'rejected') return 'bg-red-100 text-red-700';
  return 'bg-amber-100 text-amber-700';
}

export default function StaffRequisitions() {
  const [requisitions, setRequisitions] = useState([]);
  const [form, setForm] = useState(blankForm);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const { data } = await api.get('/staff/requisitions?all=1');
      setRequisitions(items(data));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load requisitions');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    if (!form.title.trim()) return toast.error('Enter the item or request');
    setSaving(true);
    try {
      const { data } = await api.post('/staff/requisitions', form);
      setRequisitions((current) => [data.requisition, ...current]);
      setForm(blankForm);
      toast.success('Requisition submitted');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit requisition');
    } finally {
      setSaving(false);
    }
  };

  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));

  return (
    <div className="space-y-4">
      <section className="surface-card p-4">
        <h1 className="page-title">My Requisitions</h1>
        <p className="page-subtitle">Request tools, equipment, or any other item needed for field work.</p>
      </section>

      <section className="surface-card p-4">
        <form className="grid gap-4 lg:grid-cols-[160px_1fr_1.4fr_auto] lg:items-end" onSubmit={submit}>
          <label className="block text-xs font-semibold text-slate-500">
            Type
            <select className="form-input" value={form.type} onChange={(event) => update('type', event.target.value)}>
              <option value="tools">Tools</option>
              <option value="equipment">Equipment</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label className="block text-xs font-semibold text-slate-500">
            Item
            <input className="form-input" value={form.title} onChange={(event) => update('title', event.target.value)} placeholder="e.g. Ladder, cable tester, router" />
          </label>
          <label className="block text-xs font-semibold text-slate-500">
            Reason
            <input className="form-input" value={form.reason} onChange={(event) => update('reason', event.target.value)} placeholder="Why is it needed?" />
          </label>
          <button type="submit" className="btn-primary h-9" disabled={saving}>
            <Plus size={14} />
            {saving ? 'Submitting...' : 'Submit'}
          </button>
        </form>
      </section>

      <section className="table-shell overflow-x-auto">
        <table className="min-w-[760px] divide-y divide-slate-200">
          <thead className="table-head">
            <tr>
              <th className="px-4 py-3">Item</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Reason</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {requisitions.length === 0 ? (
              <tr><td className="table-cell text-slate-500" colSpan="4">No requisitions submitted yet.</td></tr>
            ) : requisitions.map((item) => (
              <tr key={item.id}>
                <td className="table-cell font-medium text-slate-950">{item.title}</td>
                <td className="table-cell capitalize">{item.type}</td>
                <td className="table-cell">{item.reason || '-'}</td>
                <td className="table-cell">
                  <span className={`rounded-full px-2 py-1 text-xs font-semibold capitalize ${statusClass(item.status)}`}>{item.status || 'pending'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
