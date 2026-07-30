import { MessageSquare, Save, Send, Smartphone } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const defaults = {
  sms_enabled: true,
  sms_on_maintenance: true,
  sms_on_promotions: true,
  sms_on_payment: true,
  whatsapp_enabled: false,
  roamtech_sender_id: '',
  sms_template_maintenance: '', sms_template_promotion: '', sms_template_hotspot: '', sms_template_pppoe: '',
  sms_balance: 0, sms_sent_count: 0,
};

export default function Messages() {
  const [form, setForm] = useState(defaults);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const { data } = await api.get('/settings/notifications');
        setForm({
          sms_enabled: data.sms_enabled !== false,
          sms_on_maintenance: data.sms_on_maintenance !== false,
          sms_on_promotions: data.sms_on_promotions !== false,
          sms_on_payment: data.sms_on_payment !== false,
          whatsapp_enabled: Boolean(data.whatsapp_enabled),
          roamtech_sender_id: data.roamtech_sender_id || '',
          sms_template_maintenance: data.sms_template_maintenance || '', sms_template_promotion: data.sms_template_promotion || '', sms_template_hotspot: data.sms_template_hotspot || '', sms_template_pppoe: data.sms_template_pppoe || '', sms_balance: data.sms_balance || 0, sms_sent_count: data.sms_sent_count || 0,
        });
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load message settings');
      } finally {
        setLoading(false);
      }
    }

    load();
  }, []);

  const update = (event) => {
    const { checked, name, type, value } = event.target;
    setForm((current) => ({ ...current, [name]: type === 'checkbox' ? checked : value }));
  };

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      const { data } = await api.patch('/settings/notifications', form);
      toast.success(data.message || 'Roamtech message settings saved');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save message settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-sm font-medium text-slate-600">Loading message settings...</p>;
  }

  return (
    <div className="space-y-4">
      <section className="surface-card">
        <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="page-title">Messages</h1>
            <p className="page-subtitle">Configure Roamtech SMS and WhatsApp messages sent after Hotspot or PPPoE package payments.</p>
          </div>
          <div className="inline-flex h-9 items-center gap-2 rounded-md bg-app-navy px-3 text-sm font-medium text-white">
            <MessageSquare size={17} />
            Roamtech
          </div>
        </div>
      </section>

      <form className="surface-card p-4" onSubmit={save}>
        <div className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
          <section className="space-y-4">
            <div>
              <label className="form-label" htmlFor="roamtech_sender_id">Roamtech sender ID</label>
              <input
                id="roamtech_sender_id"
                name="roamtech_sender_id"
                className="form-input"
                value={form.roamtech_sender_id}
                onChange={update}
                placeholder="Your approved sender ID"
              />
            </div>

            <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3">
              <input className="mt-1" name="sms_enabled" type="checkbox" checked={form.sms_enabled} onChange={update} />
              <span>
                <span className="block text-sm font-medium text-slate-950">Send SMS after payment</span>
                <span className="block text-sm text-slate-600">Customers receive access details when Paystack confirms a Hotspot or PPPoE package.</span>
              </span>
            </label>
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <p className="text-sm font-medium text-slate-950">Send messages for</p>
              <div className="mt-3 space-y-3">
                <label className="flex items-center gap-3 text-sm"><input name="sms_on_maintenance" type="checkbox" checked={form.sms_on_maintenance} onChange={update} /> Maintenance notices</label>
                <label className="flex items-center gap-3 text-sm"><input name="sms_on_promotions" type="checkbox" checked={form.sms_on_promotions} onChange={update} /> Promotions</label>
                <label className="flex items-center gap-3 text-sm"><input name="sms_on_payment" type="checkbox" checked={form.sms_on_payment} onChange={update} /> Customer package purchases</label>
              </div>
            </div>

            <label className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3">
              <input className="mt-1" name="whatsapp_enabled" type="checkbox" checked={form.whatsapp_enabled} onChange={update} />
              <span>
                <span className="block text-sm font-medium text-slate-950">Send WhatsApp after payment</span>
                <span className="block text-sm text-slate-600">Use Roamtech WhatsApp messaging for customers with reachable WhatsApp numbers.</span>
              </span>
            </label>
          </section>

          <section className="space-y-4">
            <div>
              <p className="text-sm font-semibold">SMS balance: {form.sms_balance} &nbsp; | &nbsp; Sent: {form.sms_sent_count}</p>
              <label className="form-label" htmlFor="sms_template_hotspot">Hotspot package message</label>
              <textarea
                id="sms_template_hotspot"
                name="sms_template_hotspot"
                className="mt-1 min-h-28 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-app-accent focus:ring-2 focus:ring-blue-100"
                value={form.sms_template_hotspot}
                onChange={update}
              />
            </div>

            <div>
              <label className="form-label" htmlFor="sms_template_pppoe">PPPoE package message</label>
              <textarea
                id="sms_template_pppoe"
                name="sms_template_pppoe"
                className="mt-1 min-h-28 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-app-accent focus:ring-2 focus:ring-blue-100"
                value={form.sms_template_pppoe}
                onChange={update}
              />
            </div>
          </section>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <label className="form-label">Maintenance message<textarea name="sms_template_maintenance" className="mt-1 min-h-24 w-full rounded-md border p-3" value={form.sms_template_maintenance} onChange={update} /></label>
          <label className="form-label">Promotion message<textarea name="sms_template_promotion" className="mt-1 min-h-24 w-full rounded-md border p-3" value={form.sms_template_promotion} onChange={update} /></label>
        </div>

        <div className="mt-4 flex flex-col gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Smartphone size={17} className="text-app-navy" />
            <span>Available variables: {'{{name}}'}, {'{{package}}'}, {'{{amount}}'}, {'{{username}}'}, {'{{password}}'}</span>
          </div>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? <Send size={17} /> : <Save size={17} />}
            {saving ? 'Saving...' : 'Save Messages'}
          </button>
        </div>
      </form>
    </div>
  );
}
