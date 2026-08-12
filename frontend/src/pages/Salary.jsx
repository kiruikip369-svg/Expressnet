import { Banknote, ChevronDown, Pencil, Plus, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';

const initialSalaries = [
  { id: 'SAL-001', staff: 'Network Technician', role: 'Field support', amount: 28000, method: 'Mpesa', status: 'paid', paid_at: '2026-06-30T09:15:00' },
  { id: 'SAL-002', staff: 'Customer Care', role: 'Support desk', amount: 22000, method: 'Bank', status: 'paid', paid_at: '2026-06-30T10:20:00' },
  { id: 'SAL-003', staff: 'Installer', role: 'Installations', amount: 18000, method: 'Cash', status: 'pending', paid_at: '2026-07-31T08:00:00' },
];

const blankSalary = { id: '', staff: '', role: '', amount: 0, method: 'Mpesa', status: 'pending', paid_at: '' };

function toDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.valueOf()) ? date : null;
}

function formatKES(value) {
  return `Ksh ${Number(value || 0).toLocaleString('en-KE', { minimumFractionDigits: 2 })}`;
}

function formatDate(value) {
  const date = toDate(value);
  if (!date) return '-';
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function MetricCard({ title, value, helper }) {
  return (
    <div className="rounded-md bg-white p-5 shadow-[0_18px_30px_rgba(15,23,42,0.10)]">
      <p className="text-xs font-semibold text-slate-500">{title}</p>
      <div className="mt-3 flex items-center gap-2 text-black">
        <p className="text-xl font-bold">{formatKES(value)}</p>
        <Banknote size={15} style={{ color: 'var(--app-accent)' }} />
      </div>
      <p className="mt-2 text-xs text-slate-500">{helper}</p>
    </div>
  );
}

export default function Salary() {
  const [salaries, setSalaries] = useState(initialSalaries);
  const [draft, setDraft] = useState(blankSalary);
  const [editingId, setEditingId] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const needle = query.toLowerCase();
    return salaries.filter((salary) => `${salary.staff} ${salary.role} ${salary.method} ${salary.status}`.toLowerCase().includes(needle));
  }, [query, salaries]);

  const totals = useMemo(() => {
    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const paid = salaries.filter((salary) => salary.status === 'paid');
    return {
      monthly: paid.reduce((sum, salary) => (toDate(salary.paid_at) >= monthStart ? sum + Number(salary.amount || 0) : sum), 0),
      pending: salaries.filter((salary) => salary.status !== 'paid').reduce((sum, salary) => sum + Number(salary.amount || 0), 0),
      total: salaries.reduce((sum, salary) => sum + Number(salary.amount || 0), 0),
    };
  }, [salaries]);

  const openCreate = () => {
    setDraft({ ...blankSalary, paid_at: new Date().toISOString().slice(0, 16) });
    setEditingId(null);
    setModalOpen(true);
  };

  const openEdit = (salary) => {
    setDraft({ ...salary, paid_at: toDate(salary.paid_at)?.toISOString().slice(0, 16) || '' });
    setEditingId(salary.id);
    setModalOpen(true);
  };

  const save = (event) => {
    event.preventDefault();
    const payload = { ...draft, id: draft.id || `SAL-${Date.now().toString().slice(-4)}`, amount: Number(draft.amount || 0) };
    setSalaries((current) => (editingId ? current.map((item) => (item.id === editingId ? payload : item)) : [payload, ...current]));
    setModalOpen(false);
    setEditingId(null);
    setDraft(blankSalary);
    toast.success('Salary record saved');
  };

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-black">Salary</h1>
        <button type="button" className="btn-primary h-9 px-4 shadow-md" onClick={openCreate}>
          <Plus size={14} />
          Add Salary
        </button>
      </div>

      <section className="grid gap-6 md:grid-cols-3">
        <MetricCard title="Paid This Month" value={totals.monthly} helper="Completed salary payments" />
        <MetricCard title="Pending Salary" value={totals.pending} helper="Awaiting payment" />
        <MetricCard title="Payroll Total" value={totals.total} helper="All visible salary records" />
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex justify-end border-b border-slate-200 p-3">
          <label className="relative block w-full max-w-xs">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input className="form-input mt-0 h-9 pl-9" placeholder="Search salary" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[900px] w-full">
            <thead className="bg-slate-50 text-left text-xs font-semibold text-black">
              <tr>
                {['Staff', 'Role', 'Amount', 'Method', 'Status', 'Pay date'].map((heading) => (
                  <th key={heading} className="px-5 py-4">
                    <span className="inline-flex items-center gap-1">{heading}<ChevronDown size={15} className="text-slate-400" /></span>
                  </th>
                ))}
                <th className="px-5 py-4" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs text-black">
              {filtered.length === 0 ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="7">No salary records found.</td></tr>
              ) : filtered.map((salary) => (
                <tr key={salary.id}>
                  <td className="px-5 py-4 font-bold" style={{ color: 'var(--app-accent)' }}>{salary.staff}</td>
                  <td className="px-5 py-4">{salary.role || '-'}</td>
                  <td className="px-5 py-4">{formatKES(salary.amount)}</td>
                  <td className="px-5 py-4">{salary.method}</td>
                  <td className="px-5 py-4"><span className={`rounded-md border px-2 py-1 text-[10px] capitalize ${salary.status === 'paid' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>{salary.status}</span></td>
                  <td className="px-5 py-4">{formatDate(salary.paid_at)}</td>
                  <td className="px-5 py-4 text-right">
                    <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--app-accent)' }} onClick={() => openEdit(salary)}>
                      <Pencil size={14} className="text-slate-400" />
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <form className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl" onSubmit={save}>
            <h2 className="text-base font-semibold text-black">{editingId ? 'Edit Salary' : 'Add Salary'}</h2>
            <div className="mt-4 grid gap-3">
              <label className="text-xs font-semibold text-slate-600">Staff<input className="form-input" value={draft.staff} onChange={(event) => setDraft((current) => ({ ...current, staff: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Role<input className="form-input" value={draft.role} onChange={(event) => setDraft((current) => ({ ...current, role: event.target.value }))} /></label>
              <label className="text-xs font-semibold text-slate-600">Amount<input className="form-input" type="number" value={draft.amount} onChange={(event) => setDraft((current) => ({ ...current, amount: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Method<select className="form-input" value={draft.method} onChange={(event) => setDraft((current) => ({ ...current, method: event.target.value }))}><option>Mpesa</option><option>Bank</option><option>Cash</option></select></label>
              <label className="text-xs font-semibold text-slate-600">Status<select className="form-input" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}><option value="pending">Pending</option><option value="paid">Paid</option></select></label>
              <label className="text-xs font-semibold text-slate-600">Pay date<input className="form-input" type="datetime-local" value={draft.paid_at} onChange={(event) => setDraft((current) => ({ ...current, paid_at: event.target.value }))} required /></label>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setModalOpen(false)}>Cancel</button>
              <button type="submit" className="btn-primary">Save</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
