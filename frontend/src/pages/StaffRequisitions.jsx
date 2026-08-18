import { Plus, X } from 'lucide-react';
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
  const [showModal, setShowModal] = useState(false);

  const load = async () => {
    try {
      const { data } = await api.get('/staff/requisitions');
      setRequisitions(items(data));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load requisitions');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openModal = () => {
    setForm(blankForm);
    setShowModal(true);
  };

  const closeModal = () => {
    if (saving) return;
    setShowModal(false);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!form.title.trim()) return toast.error('Enter the item or request');
    setSaving(true);
    try {
      const { data } = await api.post('/staff/requisitions', form);
      setRequisitions((current) => [data.requisition, ...current]);
      setForm(blankForm);
      setShowModal(false);
      toast.success('Requisition submitted');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit requisition');
    } finally {
      setSaving(false);
    }
  };

  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));

  const latestRequisitions = requisitions.slice(0, 3);

  return (
    <div className="space-y-4">
      <section className="surface-card p-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="page-title">My Requisitions</h1>
          <p className="page-subtitle">Request tools, equipment, or any other item needed for field work.</p>
        </div>
        <button type="button" className="btn-primary h-9 shrink-0" onClick={openModal}>
          <Plus size={14} />
          Create Requisition
        </button>
      </section>

      <section className="table-shell overflow-x-auto">
        <div className="flex items-center justify-between px-4 pt-4">
          <h2 className="text-sm font-semibold text-slate-700">Latest Requisitions</h2>
          {requisitions.length > 3 && (
            <span className="text-xs text-slate-400">Showing 3 of {requisitions.length}</span>
          )}
        </div>
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
            {latestRequisitions.length === 0 ? (
              <tr><td className="table-cell text-slate-500" colSpan="4">No requisitions submitted yet.</td></tr>
            ) : latestRequisitions.map((item) => (
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

      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
          onClick={closeModal}
        >
          <div
            className="surface-card w-full max-w-lg p-5"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-900">Create Requisition</h2>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={closeModal}>
                <X size={18} />
              </button>
            </div>

            <form className="grid gap-4" onSubmit={submit}>
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

              <div className="mt-2 flex justify-end gap-2">
                <button type="button" className="btn-secondary h-9" onClick={closeModal} disabled={saving}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary h-9" disabled={saving}>
                  <Plus size={14} />
                  {saving ? 'Submitting...' : 'Submit'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}