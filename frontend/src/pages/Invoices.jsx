import { ChevronDown, FileText, Pencil, Plus, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const blankInvoice = { id: '', customer: '', item: '', amount: 0, due_at: '', status: 'draft' };

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

function statusClass(status) {
  if (status === 'paid') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  if (status === 'sent') return 'border-blue-200 bg-blue-50 text-blue-700';
  if (status === 'overdue') return 'border-red-200 bg-red-50 text-red-700';
  return 'border-slate-200 bg-slate-50 text-slate-700';
}

function MetricCard({ title, value, helper }) {
  return (
    <div className="rounded-md bg-white p-5 shadow-[0_18px_30px_rgba(15,23,42,0.10)]">
      <p className="text-xs font-semibold text-slate-500">{title}</p>
      <div className="mt-3 flex items-center gap-2 text-black">
        <p className="text-xl font-bold">{formatKES(value)}</p>
        <FileText size={15} style={{ color: 'var(--app-accent)' }} />
      </div>
      <p className="mt-2 text-xs text-slate-500">{helper}</p>
    </div>
  );
}

export default function Invoices() {
  const [invoices, setInvoices] = useState([]);
  const [draft, setDraft] = useState(blankInvoice);
  const [editingId, setEditingId] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);

  async function loadInvoices() {
    setLoading(true);
    try {
      const { data } = await api.get('/invoices?page_size=100');
      setInvoices(Array.isArray(data) ? data : data.results || []);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load invoices');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadInvoices();
  }, []);

  const filtered = useMemo(() => {
    const needle = query.toLowerCase();
    return invoices.filter((invoice) => `${invoice.customer} ${invoice.item} ${invoice.status} ${invoice.id}`.toLowerCase().includes(needle));
  }, [invoices, query]);

  const totals = useMemo(() => ({
    paid: invoices.filter((invoice) => invoice.status === 'paid').reduce((sum, invoice) => sum + Number(invoice.amount || 0), 0),
    outstanding: invoices.filter((invoice) => invoice.status !== 'paid').reduce((sum, invoice) => sum + Number(invoice.amount || 0), 0),
    total: invoices.reduce((sum, invoice) => sum + Number(invoice.amount || 0), 0),
  }), [invoices]);

  const openCreate = () => {
    setDraft({ ...blankInvoice, due_at: new Date().toISOString().slice(0, 16) });
    setEditingId(null);
    setModalOpen(true);
  };

  const openEdit = (invoice) => {
    setDraft({ ...invoice, due_at: toDate(invoice.due_at)?.toISOString().slice(0, 16) || '' });
    setEditingId(invoice.id);
    setModalOpen(true);
  };

  const save = (event) => {
    event.preventDefault();
    const payload = { ...draft, amount: Number(draft.amount || 0) };
    const request = editingId ? api.patch(`/invoices/${editingId}`, payload) : api.post('/invoices', payload);
    request.then(async () => {
      setModalOpen(false);
      setEditingId(null);
      setDraft(blankInvoice);
      toast.success('Invoice saved');
      await loadInvoices();
    }).catch((error) => {
      toast.error(error.response?.data?.message || 'Failed to save invoice');
    });
  };

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-black">Invoices</h1>
        <button type="button" className="btn-primary h-9 px-4 shadow-md" onClick={openCreate}>
          <Plus size={14} />
          Create Invoice
        </button>
      </div>

      <section className="grid gap-6 md:grid-cols-3">
        <MetricCard title="Paid Invoices" value={totals.paid} helper="Invoice value marked paid" />
        <MetricCard title="Outstanding" value={totals.outstanding} helper="Draft, sent, or overdue" />
        <MetricCard title="Total Invoiced" value={totals.total} helper="All visible invoices" />
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex justify-end border-b border-slate-200 p-3">
          <label className="relative block w-full max-w-xs">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input className="form-input mt-0 h-9 pl-9" placeholder="Search invoices" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[900px] w-full">
            <thead className="bg-slate-50 text-left text-xs font-semibold text-black">
              <tr>
                {['Invoice', 'Customer', 'Item', 'Amount', 'Due date', 'Status'].map((heading) => (
                  <th key={heading} className="px-5 py-4">
                    <span className="inline-flex items-center gap-1">{heading}<ChevronDown size={15} className="text-slate-400" /></span>
                  </th>
                ))}
                <th className="px-5 py-4" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs text-black">
              {loading ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="7">Loading invoices...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="7">No invoices found.</td></tr>
              ) : filtered.map((invoice) => (
                <tr key={invoice.id}>
                  <td className="px-5 py-4 font-bold" style={{ color: 'var(--app-accent)' }}>{invoice.invoice_number || invoice.id}</td>
                  <td className="px-5 py-4">{invoice.customer || invoice.customer_name}</td>
                  <td className="px-5 py-4">{invoice.item}</td>
                  <td className="px-5 py-4">{formatKES(invoice.amount)}</td>
                  <td className="px-5 py-4">{formatDate(invoice.due_at)}</td>
                  <td className="px-5 py-4"><span className={`rounded-md border px-2 py-1 text-[10px] capitalize ${statusClass(invoice.status)}`}>{invoice.status}</span></td>
                  <td className="px-5 py-4 text-right">
                    <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--app-accent)' }} onClick={() => openEdit(invoice)}>
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
            <h2 className="text-base font-semibold text-black">{editingId ? 'Edit Invoice' : 'Create Invoice'}</h2>
            <div className="mt-4 grid gap-3">
              <label className="text-xs font-semibold text-slate-600">Customer<input className="form-input" value={draft.customer} onChange={(event) => setDraft((current) => ({ ...current, customer: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Item<input className="form-input" value={draft.item} onChange={(event) => setDraft((current) => ({ ...current, item: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Amount<input className="form-input" type="number" value={draft.amount} onChange={(event) => setDraft((current) => ({ ...current, amount: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Due date<input className="form-input" type="datetime-local" value={draft.due_at} onChange={(event) => setDraft((current) => ({ ...current, due_at: event.target.value }))} required /></label>
              <label className="text-xs font-semibold text-slate-600">Status<select className="form-input" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}><option value="draft">Draft</option><option value="sent">Sent</option><option value="paid">Paid</option><option value="overdue">Overdue</option></select></label>
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
