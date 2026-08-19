import { CreditCard, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '-' : date.toLocaleDateString();
}

function formatKES(value) {
  return `KES ${Number(value || 0).toLocaleString('en-KE')}`;
}

export default function BillingPayment() {
  const [subscription, setSubscription] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ reference: '', method: 'manual', notes: '' });

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/subscription/status');
      setSubscription(data.subscription);
      if (!data.payment_required && data.subscription?.status === 'active') {
        toast.success('Subscription is active');
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load subscription');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    if (!form.reference.trim()) {
      toast.error('Enter your payment reference');
      return;
    }
    setSaving(true);
    try {
      await api.post('/subscription/status', {
        amount: subscription?.amount || 0,
        currency: subscription?.currency || 'KES',
        ...form,
      });
      toast.success('Payment recorded. Service restored.');
      window.location.assign('/dashboard');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit payment');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <section className="theme-card rounded-lg border p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="theme-text text-xl font-semibold">Subscription Payment Required</h1>
            <p className="theme-muted mt-1 text-sm">
              Your trial or grace period has ended. Pay your system subscription to continue using the billing workspace.
            </p>
          </div>
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-md" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>
            <CreditCard size={20} />
          </span>
        </div>

        {loading ? (
          <p className="theme-muted mt-6 text-sm">Loading subscription...</p>
        ) : (
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <div className="theme-card-muted rounded-md border p-3">
              <p className="theme-muted text-xs">Plan</p>
              <p className="theme-text mt-1 text-lg font-semibold capitalize">{subscription?.plan || 'basic'}</p>
            </div>
            <div className="theme-card-muted rounded-md border p-3">
              <p className="theme-muted text-xs">Amount Due</p>
              <p className="theme-text mt-1 text-lg font-semibold">{formatKES(subscription?.amount)}</p>
            </div>
            <div className="theme-card-muted rounded-md border p-3">
              <p className="theme-muted text-xs">Expired On</p>
              <p className="theme-text mt-1 text-lg font-semibold">{formatDate(subscription?.expires_at)}</p>
            </div>
          </div>
        )}
        {!loading && subscription?.status === 'active' && (
          <button type="button" className="btn-primary mt-5" onClick={() => window.location.assign('/dashboard')}>
            Continue to Dashboard
          </button>
        )}
      </section>

      <form className="theme-card rounded-lg border p-5" onSubmit={submit}>
        <h2 className="theme-text text-sm font-semibold">Submit Payment</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="text-xs font-semibold text-slate-500">
            Payment method
            <select className="form-input" value={form.method} onChange={(event) => setForm((current) => ({ ...current, method: event.target.value }))}>
              <option value="manual">Manual</option>
              <option value="mpesa">M-Pesa</option>
              <option value="bank">Bank</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-500">
            Payment reference
            <input className="form-input" value={form.reference} onChange={(event) => setForm((current) => ({ ...current, reference: event.target.value }))} placeholder="Receipt, transaction, or invoice reference" />
          </label>
          <label className="text-xs font-semibold text-slate-500 sm:col-span-2">
            Notes
            <textarea className="form-input min-h-20" value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} placeholder="Optional payment details" />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={load} disabled={loading || saving}>
            <RefreshCw size={14} />
            Refresh
          </button>
          <button type="submit" className="btn-primary" disabled={saving || loading}>
            {saving ? 'Submitting...' : 'Submit Payment'}
          </button>
        </div>
      </form>
    </div>
  );
}
