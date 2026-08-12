import { CheckCircle2, Search, Trash2, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const statuses = ['pending', 'approved', 'issued', 'rejected'];

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

function statusClass(status) {
  if (status === 'approved') return 'bg-blue-100 text-blue-700';
  if (status === 'issued') return 'bg-emerald-100 text-emerald-700';
  if (status === 'rejected') return 'bg-red-100 text-red-700';
  return 'bg-amber-100 text-amber-700';
}

export default function Requisitions() {
  const [requisitions, setRequisitions] = useState([]);
  const [query, setQuery] = useState('');
  const [busyId, setBusyId] = useState('');

  const load = async () => {
    try {
      const { data } = await api.get('/requisitions?all=1');
      setRequisitions(items(data));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load requisitions');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const value = query.toLowerCase();
    return requisitions.filter((item) => `${item.title} ${item.type} ${item.reason} ${item.requested_by_name} ${item.status}`.toLowerCase().includes(value));
  }, [query, requisitions]);

  const updateStatus = async (requisition, status) => {
    setBusyId(requisition.id);
    try {
      const { data } = await api.patch(`/requisitions/${requisition.id}`, { status });
      setRequisitions((current) => current.map((item) => (item.id === requisition.id ? data.requisition : item)));
      toast.success(`Requisition ${status}`);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to update requisition');
    } finally {
      setBusyId('');
    }
  };

  const remove = async (requisition) => {
    if (!window.confirm(`Delete requisition "${requisition.title}"?`)) return;
    setBusyId(requisition.id);
    try {
      await api.delete(`/requisitions/${requisition.id}`);
      setRequisitions((current) => current.filter((item) => item.id !== requisition.id));
      toast.success('Requisition deleted');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete requisition');
    } finally {
      setBusyId('');
    }
  };

  return (
    <div className="space-y-4">
      <section className="surface-card p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="page-title">Requisitions</h1>
            <p className="page-subtitle">Review staff requests for tools, equipment, fuel, and other field work needs.</p>
          </div>
          <label className="relative block w-full sm:w-80">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <input className="form-input mt-0 pl-9" placeholder="Search requisitions" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
      </section>

      <section className="table-shell overflow-x-auto">
        <table className="min-w-[980px] divide-y divide-slate-200">
          <thead className="table-head">
            <tr>
              <th className="px-4 py-3">Item</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Qty</th>
              <th className="px-4 py-3">Requested by</th>
              <th className="px-4 py-3">Reason</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.length === 0 ? (
              <tr><td className="table-cell text-slate-500" colSpan={7}>No requisitions found.</td></tr>
            ) : filtered.map((requisition) => (
              <tr key={requisition.id}>
                <td className="table-cell font-medium text-slate-950">{requisition.title}</td>
                <td className="table-cell capitalize">{requisition.type}</td>
                <td className="table-cell">{requisition.quantity}</td>
                <td className="table-cell">{requisition.requested_by_name || '-'}</td>
                <td className="table-cell max-w-xs truncate">{requisition.reason || '-'}</td>
                <td className="table-cell">
                  <span className={`rounded-full px-2 py-1 text-xs font-semibold capitalize ${statusClass(requisition.status)}`}>{requisition.status || 'pending'}</span>
                </td>
                <td className="table-cell">
                  <div className="flex justify-end gap-2">
                    {statuses.map((status) => (
                      <button key={status} type="button" className="btn-secondary" onClick={() => updateStatus(requisition, status)} disabled={busyId === requisition.id || requisition.status === status}>
                        {status === 'rejected' ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
                        {status}
                      </button>
                    ))}
                    <button type="button" className="btn-danger" onClick={() => remove(requisition)} disabled={busyId === requisition.id}>
                      <Trash2 size={14} />
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
