import { ChevronDown, CreditCard, Eye, Pencil, Phone, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { useSearchParams } from 'react-router-dom';
import api from '../api/axios';

const blankExpense = { id: '', type: 'SYSTEM_PAYMENT', amount: 0, method: 'Mpesa', date: '' };

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
    <div className="rounded-md p-5 shadow-[0_18px_30px_rgba(15,23,42,0.10)]" style={{ background: 'var(--app-accent-soft)', color: 'var(--app-text)' }}>
      <p className="text-xs font-semibold">{title}</p>
      <div className="mt-3 flex items-center gap-2">
        <p className="text-xl font-bold">{formatKES(value)}</p>
        <Eye size={14} />
      </div>
      <p className="mt-2 text-xs">{helper}</p>
    </div>
  );
}

export default function Expenses() {
  const [searchParams] = useSearchParams();
  const [expenses, setExpenses] = useState([]);
  const [subscription, setSubscription] = useState(null);
  const [draft, setDraft] = useState(blankExpense);
  const [editingId, setEditingId] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [payModalOpen, setPayModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [query, setQuery] = useState('');
  const [phone, setPhone] = useState('');
  const [payingSystem, setPayingSystem] = useState(false);
  const [loadingSubscription, setLoadingSubscription] = useState(true);

  const loadSubscription = async () => {
    setLoadingSubscription(true);
    try {
      const { data } = await api.get('/subscription/status');
      setSubscription(data.subscription);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load system subscription');
    } finally {
      setLoadingSubscription(false);
    }
  };

  useEffect(() => {
    loadSubscription();
  }, []);

  // Auto-open the pay modal if ?paySystem is present in the URL
  useEffect(() => {
    if (searchParams.get('paySystem')) {
      setPayModalOpen(true);
    }
  }, [searchParams]);

  const filtered = useMemo(() => {
    const needle = query.toLowerCase();
    return expenses.filter((expense) => `${expense.type} ${expense.method} ${expense.amount}`.toLowerCase().includes(needle));
  }, [expenses, query]);

  const totals = useMemo(() => {
    const now = new Date();
    const weekStart = new Date(now);
    weekStart.setDate(now.getDate() - now.getDay());
    weekStart.setHours(0, 0, 0, 0);
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const yearStart = new Date(now.getFullYear(), 0, 1);
    const sumSince = (start) => expenses.reduce((sum, expense) => {
      const date = toDate(expense.date);
      return date && date >= start ? sum + Number(expense.amount || 0) : sum;
    }, 0);
    return {
      yearly: sumSince(yearStart),
      monthly: sumSince(monthStart),
      weekly: sumSince(weekStart),
    };
  }, [expenses]);

  const openCreate = () => {
    setDraft({ ...blankExpense, date: new Date().toISOString().slice(0, 16) });
    setEditingId(null);
    setModalOpen(true);
  };

  const openEdit = (expense) => {
    setDraft({ ...expense, date: toDate(expense.date)?.toISOString().slice(0, 16) || '' });
    setEditingId(expense.id);
    setModalOpen(true);
  };

  const save = (event) => {
    event.preventDefault();
    const payload = { ...draft, id: draft.id || `EXP-${Date.now().toString().slice(-4)}`, amount: Number(draft.amount || 0) };
    setExpenses((current) => (editingId ? current.map((item) => (item.id === editingId ? payload : item)) : [payload, ...current]));
    setModalOpen(false);
    setEditingId(null);
    setDraft(blankExpense);
    toast.success('Expense saved');
  };

  const confirmDelete = (expense) => {
    setDeleteTarget(expense);
  };

  const deleteExpense = () => {
    if (!deleteTarget) return;
    setExpenses((current) => current.filter((item) => item.id !== deleteTarget.id));
    toast.success('Expense deleted');
    setDeleteTarget(null);
  };

  const paySystem = async (event) => {
    event.preventDefault();
    if (!phone.trim()) {
      toast.error('Enter the M-Pesa phone number');
      return;
    }
    setPayingSystem(true);
    try {
      const { data } = await api.post('/subscription/status', {
        method: 'mpesa_stk',
        phone,
        amount: subscription?.amount || 0,
        currency: subscription?.currency || 'KES',
      });
      toast.success(data.message || 'STK push sent. Complete payment on your phone.');
      await loadSubscription();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to start M-Pesa payment');
    } finally {
      setPayingSystem(false);
    }
  };

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-black">Expenses</h1>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary h-9 px-4" onClick={() => setPayModalOpen(true)}>
            <CreditCard size={14} />
            Pay System
          </button>
          <button type="button" className="btn-primary h-9 px-4 shadow-md" onClick={openCreate}>
            <Plus size={14} />
            Create Expense
          </button>
        </div>
      </div>

      <section className="grid gap-6 md:grid-cols-3">
        <MetricCard title="Yearly Expenses" value={totals.yearly} helper="Total expenses this year" />
        <MetricCard title="Monthly Expenses" value={totals.monthly} helper="Total expenses this month" />
        <MetricCard title="Weekly Expenses" value={totals.weekly} helper="Total expenses this week" />
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex justify-end border-b border-slate-200 p-3">
          <label className="relative block w-full max-w-xs">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input className="form-input mt-0 h-9 pl-9" placeholder="Search" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[820px] w-full">
            <thead className="bg-slate-50 text-left text-xs font-semibold text-black">
              <tr>
                <th className="w-12 px-5 py-4"><input type="checkbox" className="h-4 w-4 rounded border-slate-300" /></th>
                {['Date', 'Type', 'Amount', 'Method'].map((heading) => (
                  <th key={heading} className="px-5 py-4">
                    <span className="inline-flex items-center gap-1">{heading}<ChevronDown size={15} className="text-slate-400" /></span>
                  </th>
                ))}
                <th className="px-5 py-4" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs text-black">
              {filtered.length === 0 ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="6">No expenses found.</td></tr>
              ) : filtered.map((expense) => (
                <tr key={expense.id}>
                  <td className="px-5 py-4"><input type="checkbox" className="h-4 w-4 rounded border-slate-300" /></td>
                  <td className="px-5 py-4">{formatDate(expense.date)}</td>
                  <td className="px-5 py-4">{expense.type}</td>
                  <td className="px-5 py-4">{formatKES(expense.amount)}</td>
                  <td className="px-5 py-4"><span className="rounded-md border px-2 py-1 text-[10px]" style={{ borderColor: 'var(--app-accent-soft)', background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>{expense.method}</span></td>
                  <td className="px-5 py-4 text-right">
                    <div className="flex items-center justify-end gap-3">
                      <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--app-accent)' }} onClick={() => openEdit(expense)}>
                        <Pencil size={14} className="text-slate-400" />
                        Edit
                      </button>
                      <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold text-red-600" onClick={() => confirmDelete(expense)}>
                        <Trash2 size={14} className="text-red-400" />
                        Delete
                      </button>
                    </div>
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
            <h2 className="text-base font-semibold text-black">{editingId ? 'Edit Expense' : 'Create Expense'}</h2>
            <div className="mt-4 grid gap-3">
              <label className="text-xs font-semibold text-slate-600">Date<input className="form-input" type="datetime-local" value={draft.date} onChange={(event) => setDraft((current) => ({ ...current, date: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Type<input className="form-input" value={draft.type} onChange={(event) => setDraft((current) => ({ ...current, type: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Amount<input className="form-input" type="number" value={draft.amount} onChange={(event) => setDraft((current) => ({ ...current, amount: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Method<select className="form-input" value={draft.method} onChange={(event) => setDraft((current) => ({ ...current, method: event.target.value }))}><option>Mpesa</option><option>Cash</option><option>Bank</option></select></label>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setModalOpen(false)}>Cancel</button>
              <button type="submit" className="btn-primary">Save</button>
            </div>
          </form>
        </div>
      )}

      {payModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-md" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>
                  <CreditCard size={18} />
                </span>
                <div>
                  <h2 className="text-base font-semibold text-black">Pay System Subscription</h2>
                  <p className="text-xs text-slate-500">Enter your M-Pesa phone number to receive an STK push.</p>
                </div>
              </div>
              <button type="button" className="text-slate-400 hover:text-slate-600" onClick={() => setPayModalOpen(false)}>
                <X size={18} />
              </button>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              <div className="rounded-md border p-3" style={{ borderColor: 'var(--app-border)', background: 'var(--app-panel-muted)' }}>
                <p className="text-xs text-slate-500">Plan</p>
                <p className="mt-1 text-sm font-semibold capitalize text-black">{subscription?.plan || '-'}</p>
              </div>
              <div className="rounded-md border p-3" style={{ borderColor: 'var(--app-border)', background: 'var(--app-panel-muted)' }}>
                <p className="text-xs text-slate-500">Amount</p>
                <p className="mt-1 text-sm font-semibold text-black">{formatKES(subscription?.amount)}</p>
              </div>
              <div className="rounded-md border p-3" style={{ borderColor: 'var(--app-border)', background: 'var(--app-panel-muted)' }}>
                <p className="text-xs text-slate-500">Expires</p>
                <p className="mt-1 text-sm font-semibold text-black">{subscription?.expires_at ? formatDate(subscription.expires_at) : '-'}</p>
              </div>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              System fee: {subscription?.system_fee_percent ?? 0}% of this month&apos;s successful internet sales ({formatKES(subscription?.system_fee_sales_basis)}).
            </p>

            <form className="mt-4" onSubmit={paySystem}>
              <label className="text-xs font-semibold text-slate-600">
                M-Pesa phone number
                <div className="relative mt-1">
                  <Phone size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input className="form-input mt-0 pl-9" value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="07XXXXXXXX or 2547XXXXXXXX" />
                </div>
              </label>
              <div className="mt-4 flex justify-end gap-2">
                <button type="button" className="btn-secondary" onClick={loadSubscription} disabled={loadingSubscription || payingSystem}>
                  <RefreshCw size={14} />
                  Refresh
                </button>
                <button type="submit" className="btn-primary" disabled={loadingSubscription || payingSystem}>
                  {payingSystem ? 'Sending STK...' : 'Pay System'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <div className="w-full max-w-sm rounded-lg bg-white p-5 shadow-xl">
            <h2 className="text-base font-semibold text-black">Delete Expense</h2>
            <p className="mt-2 text-sm text-slate-600">
              Are you sure you want to delete the expense of <span className="font-semibold text-black">{formatKES(deleteTarget.amount)}</span> ({deleteTarget.type})? This action cannot be undone.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button type="button" className="inline-flex items-center gap-1 rounded-md bg-red-600 px-4 py-2 text-xs font-semibold text-white hover:bg-red-700" onClick={deleteExpense}>
                <Trash2 size={14} />
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
