import {
  AlertTriangle,
  Info,
  MessageSquare,
  Save,
  Send,
  ShoppingCart,
  Smartphone,
  Wifi,
} from 'lucide-react';
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
  sms_template_maintenance: '',
  sms_template_promotion: '',
  sms_template_hotspot: '',
  sms_template_pppoe: '',
  sms_balance: 0,
  sms_sent_count: 0,
};

const PLACEHOLDERS = [
  { tag: '{{name}}', label: "Customer's name" },
  { tag: '{{username}}', label: 'Hotspot/PPPoE username' },
  { tag: '{{password}}', label: 'Hotspot/PPPoE password' },
  { tag: '{{package}}', label: 'Package name' },
  { tag: '{{amount}}', label: 'Amount paid' },
];

function Toggle({ checked, disabled, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-app-accent focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? 'bg-app-navy' : 'bg-slate-200'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

function charCount(text) {
  const len = text?.length || 0;
  return { chars: len, parts: Math.max(1, Math.ceil(len / 160)) };
}

export default function Messages() {
  const [form, setForm] = useState(defaults);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('types');

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
          sms_template_maintenance: data.sms_template_maintenance || '',
          sms_template_promotion: data.sms_template_promotion || '',
          sms_template_hotspot: data.sms_template_hotspot || '',
          sms_template_pppoe: data.sms_template_pppoe || '',
          sms_balance: data.sms_balance || 0,
          sms_sent_count: data.sms_sent_count || 0,
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

  const toggle = (name) => {
    setForm((current) => ({ ...current, [name]: !current[name] }));
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

  // Rows for the Notification Types table. Each maps to a real field on `form`,
  // and optionally to one of the sms_template_* fields shown in the Templates tab.
  const notificationRows = [
    {
      id: 'payment_hotspot',
      title: 'Package Purchase (Hotspot)',
      description: 'Sent when a customer pays for a Hotspot package',
      icon: ShoppingCart,
      iconBg: 'bg-blue-50',
      iconColor: 'text-blue-500',
      toggleField: 'sms_on_payment',
      templateField: 'sms_template_hotspot',
      templateLabel: 'Hotspot package message',
    },
    {
      id: 'payment_pppoe',
      title: 'Package Purchase (PPPoE)',
      description: 'Sent when a customer pays for a PPPoE package',
      icon: ShoppingCart,
      iconBg: 'bg-blue-50',
      iconColor: 'text-blue-500',
      toggleField: 'sms_on_payment',
      templateField: 'sms_template_pppoe',
      templateLabel: 'PPPoE package message',
    },
    {
      id: 'maintenance',
      title: 'Maintenance Notices',
      description: 'Sent to customers ahead of planned network maintenance',
      icon: AlertTriangle,
      iconBg: 'bg-amber-50',
      iconColor: 'text-amber-500',
      toggleField: 'sms_on_maintenance',
      templateField: 'sms_template_maintenance',
      templateLabel: 'Maintenance message',
    },
    {
      id: 'promotions',
      title: 'Promotions',
      description: 'Sent for promotional offers and announcements',
      icon: MessageSquare,
      iconBg: 'bg-teal-50',
      iconColor: 'text-teal-500',
      toggleField: 'sms_on_promotions',
      templateField: 'sms_template_promotion',
      templateLabel: 'Promotion message',
    },
    {
      id: 'whatsapp',
      title: 'WhatsApp Messaging',
      description: 'Send payment confirmations over WhatsApp instead of SMS',
      icon: Smartphone,
      iconBg: 'bg-violet-50',
      iconColor: 'text-violet-500',
      toggleField: 'whatsapp_enabled',
      templateField: null,
      templateLabel: null,
    },
  ];

  const templateCards = notificationRows.filter((row) => row.templateField);

  return (
    <div className="space-y-4">
      <section className="surface-card">
        <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="page-title">Messages</h1>
            <p className="page-subtitle">
              Configure Roamtech SMS and WhatsApp messages sent after Hotspot or PPPoE package payments.
            </p>
          </div>
          <div className="inline-flex h-9 items-center gap-2 rounded-md bg-app-navy px-3 text-sm font-medium text-white">
            <MessageSquare size={17} />
            Roamtech
          </div>
        </div>
      </section>

      <form className="surface-card" onSubmit={save}>
        {/* Master switch + sender ID + balance strip */}
        <div className="flex flex-col gap-4 border-b border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between">
          <label className="flex items-center gap-3">
            <Toggle checked={form.sms_enabled} onChange={() => toggle('sms_enabled')} />
            <span>
              <span className="block text-sm font-medium text-slate-950">SMS notifications enabled</span>
              <span className="block text-xs text-slate-500">Turn off to pause all outgoing SMS, regardless of the settings below.</span>
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-4">
            <div className="min-w-[180px]">
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
            <p className="whitespace-nowrap text-sm font-semibold text-slate-700">
              Balance: {form.sms_balance} &nbsp;|&nbsp; Sent: {form.sms_sent_count}
            </p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-6 border-b border-slate-200 px-4">
          <button
            type="button"
            onClick={() => setTab('types')}
            className={`relative py-3.5 text-sm font-medium transition-colors ${
              tab === 'types' ? 'text-app-navy' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Notification Types
            {tab === 'types' && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-app-navy" />}
          </button>
          <button
            type="button"
            onClick={() => setTab('templates')}
            className={`relative py-3.5 text-sm font-medium transition-colors ${
              tab === 'templates' ? 'text-app-navy' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Message Templates
            {tab === 'templates' && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-app-navy" />}
          </button>
        </div>

        {/* Notification Types */}
        {tab === 'types' && (
          <div className="p-4">
            <p className="mb-3 text-xs text-slate-500">Select which events send an SMS to your customers.</p>
            <div className="overflow-hidden rounded-lg border border-slate-200">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/70 text-xs font-medium text-slate-500">
                    <th className="px-4 py-2.5 font-medium">Notification</th>
                    <th className="px-4 py-2.5 font-medium">Description</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {notificationRows.map((row, idx) => {
                    const Icon = row.icon;
                    const checked = form[row.toggleField];
                    return (
                      <tr
                        key={row.id}
                        className={`${idx !== notificationRows.length - 1 ? 'border-b border-slate-100' : ''} hover:bg-slate-50/60`}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2.5">
                            <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${row.iconBg}`}>
                              <Icon className={`h-3.5 w-3.5 ${row.iconColor}`} strokeWidth={2.25} />
                            </span>
                            <span className="font-medium text-slate-800">{row.title}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-slate-500">{row.description}</td>
                        <td className="px-4 py-3">
                          <Toggle
                            checked={checked}
                            disabled={!form.sms_enabled && row.id !== 'whatsapp'}
                            onChange={() => toggle(row.toggleField)}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Message Templates */}
        {tab === 'templates' && (
          <div className="grid grid-cols-1 gap-5 p-4 lg:grid-cols-[1fr_260px]">
            <div>
              <p className="mb-3 text-xs text-slate-500">Edit the SMS sent for each event. Changes save with the button below.</p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {templateCards.map((row) => {
                  const Icon = row.icon;
                  const { chars, parts } = charCount(form[row.templateField]);
                  return (
                    <div key={row.id} className="rounded-lg border border-slate-200 p-4">
                      <div className="mb-2.5 flex items-center gap-2">
                        <span className={`flex h-6 w-6 items-center justify-center rounded-full ${row.iconBg}`}>
                          <Icon className={`h-3 w-3 ${row.iconColor}`} strokeWidth={2.25} />
                        </span>
                        <span className="text-sm font-medium text-slate-800">{row.templateLabel}</span>
                      </div>
                      <textarea
                        name={row.templateField}
                        value={form[row.templateField]}
                        onChange={update}
                        className="min-h-24 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-xs leading-relaxed text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-app-accent focus:ring-2 focus:ring-blue-100"
                        placeholder={`Write the ${row.templateLabel.toLowerCase()}...`}
                      />
                      <p className="mt-2 text-[11px] text-slate-400">
                        Characters: {chars} &nbsp;|&nbsp; Parts: {parts}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Sidebar */}
            <div className="space-y-4">
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-4">
                <div className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-slate-800">
                  <Info className="h-3.5 w-3.5 text-slate-400" />
                  About SMS Notifications
                </div>
                <p className="text-xs leading-relaxed text-slate-500">
                  Keep your customers informed about package purchases, network maintenance, and offers.
                </p>
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <p className="mb-2.5 text-sm font-semibold text-slate-800">Available Placeholders</p>
                <p className="mb-3 text-xs text-slate-500">Use these placeholders in your templates:</p>
                <ul className="space-y-1.5">
                  {PLACEHOLDERS.map((p) => (
                    <li key={p.tag} className="flex items-baseline justify-between gap-2 text-xs">
                      <code className="font-mono text-rose-500">{p.tag}</code>
                      <span className="text-right text-slate-500">{p.label}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-3 border-t border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Wifi size={17} className="text-app-navy" />
            <span>Roamtech is used for both Hotspot and PPPoE notifications.</span>
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