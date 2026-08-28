import {
  AlarmClock,
  Bell,
  CheckCircle2,
  ExternalLink,
  MessageCircle,
  MessageSquare,
  MoreVertical,
  PlugZap,
  Save,
  Send,
  Settings,
  ShoppingCart,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const MASKED = '********';

const defaults = {
  provider: 'slek',
  whatsapp_enabled: true,
  sms_on_payment: true,
  whatsapp_on_customer_created: true,
  whatsapp_on_expiry: true,
  sms_on_maintenance: true,
  sms_on_promotions: true,
  apiwap_base_url: 'https://api.apiwap.com/api/v1',
  apiwap_api_key: '',
  customer_created_whatsapp_template: 'Your internet account has been created successfully.',
  payment_whatsapp_template: 'Your internet package is active. Thank you for your payment.',
  expiry_whatsapp_template: 'Your internet package is about to expire. Please renew to stay connected.',
  sms_template_maintenance: 'We will be performing scheduled maintenance. Thank you for your patience.',
  sms_template_promotion: 'Special offer from our team. Contact support for details.',
};

const providers = [
  {
    id: 'slek',
    name: 'Slek',
    country: 'Kenya',
    channel: 'WhatsApp',
    badge: 'Default',
    accent: 'border-l-4 border-l-sky-500',
    logo: <span className="text-xl font-bold tracking-tight"><span className="text-sky-600">slek</span></span>,
    configureUrl: '#',
  },
  {
    id: 'apiwap',
    name: 'ApiWap',
    country: 'Kenya',
    channel: 'WhatsApp',
    badge: 'Primary',
    accent: 'border-l-4 border-l-emerald-500',
    logo: <span className="text-xl font-bold tracking-tight"><span className="text-orange-500">api</span><span className="text-sky-500">wap</span></span>,
    configureUrl: 'https://account.apiwap.com/register',
  },
  {
    id: 'africastalking',
    name: "Africa's Talking",
    channel: 'SMS',
    logo: <span className="text-sm font-extrabold leading-none"><span className="text-emerald-600">Africa's</span><br /><span className="text-orange-500">Talking</span></span>,
    configureUrl: '#',
  },
  {
    id: 'twilio',
    name: 'Twilio',
    channel: 'SMS / WhatsApp',
    logo: <span className="text-xl font-extrabold text-red-500">twilio</span>,
    configureUrl: '#',
  },
];

const tabs = [
  ['types', Bell, 'Notification Types'],
  ['templates', MessageSquare, 'Templates'],
  ['providers', PlugZap, 'Providers / APIs'],
];

const notificationTypes = [
  ['whatsapp_on_customer_created', PlugZap, 'Customer created', 'Sent when a PPPoE or Static customer account is created.'],
  ['sms_on_payment', ShoppingCart, 'Package payments', 'Sent after customer payment and internet activation.'],
  ['whatsapp_on_expiry', AlarmClock, 'Package expiry', 'Sent before a customer package expires.'],
  ['sms_on_maintenance', AlarmClock, 'Maintenance notices', 'Use when notifying customers about planned service work.'],
  ['sms_on_promotions', MessageCircle, 'Promotions', 'Use for offers, discounts, and customer updates.'],
];

const templateFields = [
  ['customer_created_whatsapp_template', 'Customer created message', 'Customer name, package, and payable amount are added automatically. Credentials are sent to the assigned technician.'],
  ['payment_whatsapp_template', 'Payment message', 'Customer name, package, amount, username, and password are added automatically.'],
  ['expiry_whatsapp_template', 'Expiry message', 'Customer name, package, username, and expiry time are added automatically.'],
  ['sms_template_maintenance', 'Maintenance message', 'Plain message for planned downtime or service work.'],
  ['sms_template_promotion', 'Promotion message', 'Plain message for offers and general announcements.'],
];

function Toggle({ checked, onChange }) {
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={onChange} className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${checked ? 'bg-violet-600' : 'bg-slate-200'}`}>
      <span className={`h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  );
}

function ProviderBadge({ provider }) {
  const connectedProvider = provider.id === 'apiwap' || provider.id === 'slek';
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${connectedProvider ? 'bg-emerald-50 text-emerald-600' : 'bg-violet-50 text-violet-600'}`}>
      <MessageSquare size={12} />
      {provider.channel}
    </span>
  );
}

function cleanTemplate(text) {
  return String(text || '').replace(/\{\{[^}]+\}\}/g, '').replace(/\s{2,}/g, ' ').trim();
}

export default function Messages() {
  const [form, setForm] = useState(defaults);
  const [activeTab, setActiveTab] = useState('providers');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);

  const activeProvider = useMemo(() => providers.find((provider) => provider.id === form.provider) || providers[0], [form.provider]);
  const connected = Boolean(form.apiwap_api_key);

  useEffect(() => {
    async function load() {
      try {
        const { data } = await api.get('/settings/notifications');
        setForm({
          ...defaults,
          provider: data.provider || 'slek',
          whatsapp_enabled: data.whatsapp_enabled !== false,
          sms_on_payment: data.sms_on_payment !== false,
          whatsapp_on_customer_created: data.whatsapp_on_customer_created !== false,
          whatsapp_on_expiry: data.whatsapp_on_expiry !== false,
          sms_on_maintenance: data.sms_on_maintenance !== false,
          sms_on_promotions: data.sms_on_promotions !== false,
          apiwap_base_url: data.apiwap_base_url || defaults.apiwap_base_url,
          apiwap_api_key: data.has_apiwap_api_key ? MASKED : '',
          customer_created_whatsapp_template: cleanTemplate(data.customer_created_whatsapp_template) || defaults.customer_created_whatsapp_template,
          payment_whatsapp_template: cleanTemplate(data.payment_whatsapp_template) || defaults.payment_whatsapp_template,
          expiry_whatsapp_template: cleanTemplate(data.expiry_whatsapp_template) || defaults.expiry_whatsapp_template,
          sms_template_maintenance: cleanTemplate(data.sms_template_maintenance) || defaults.sms_template_maintenance,
          sms_template_promotion: cleanTemplate(data.sms_template_promotion) || defaults.sms_template_promotion,
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
    const { name, value } = event.target;
    const nextValue = name.includes('template') ? cleanTemplate(value) : value;
    setForm((current) => ({ ...current, [name]: nextValue }));
  };

  const toggle = (name) => {
    setForm((current) => ({ ...current, [name]: !current[name] }));
  };

  const saveSettings = async (nextForm = form) => {
    setSaving(true);
    try {
      const { data } = await api.patch('/settings/notifications', {
        ...nextForm,
        sms_enabled: nextForm.whatsapp_enabled,
        whatsapp_enabled: nextForm.whatsapp_enabled,
        payment_sms_template: nextForm.payment_whatsapp_template,
      });
      toast.success(data.message || 'Message settings saved');
      setForm((current) => ({
        ...current,
        provider: data.provider || nextForm.provider,
        whatsapp_enabled: data.whatsapp_enabled !== false,
        apiwap_base_url: data.apiwap_base_url || nextForm.apiwap_base_url,
        apiwap_api_key: data.has_apiwap_api_key ? MASKED : '',
      }));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save message settings');
    } finally {
      setSaving(false);
    }
  };

  const save = async (event) => {
    event.preventDefault();
    await saveSettings();
  };

  const saveApiKey = async () => {
    await saveSettings({ ...form, provider: 'apiwap', whatsapp_enabled: true });
    setConfigOpen(false);
  };

  const testConnection = async (provider = activeProvider) => {
    setTesting(true);
    try {
      let testForm = form;
      if (provider.id === 'apiwap' && form.apiwap_api_key && form.apiwap_api_key !== MASKED) {
        await saveSettings({ ...form, provider: 'apiwap', whatsapp_enabled: true });
        testForm = { ...form, provider: 'apiwap', whatsapp_enabled: true };
      }
      const { data } = await api.post('/settings/test-whatsapp', {
        provider: provider.id,
        whatsapp_enabled: true,
        apiwap_base_url: testForm.apiwap_base_url,
        apiwap_api_key: testForm.apiwap_api_key,
        message: `${provider.name} test WhatsApp notification from your billing system.`,
      });
      toast.success(data.message || `${provider.name} connection tested`);
    } catch (error) {
      toast.error(error.response?.data?.message || `${provider.name} test failed`);
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <p className="text-sm font-medium text-slate-600">Loading message settings...</p>;

  return (
    <form className="space-y-4" onSubmit={save}>
      <section className="surface-card overflow-hidden">
        <div className="grid grid-cols-3">
          {tabs.map(([key, Icon, label]) => (
            <button key={key} type="button" onClick={() => setActiveTab(key)} className={`relative flex h-14 items-center justify-center gap-2 text-xs font-semibold transition ${activeTab === key ? 'text-violet-600' : 'text-slate-500 hover:text-slate-800'}`}>
              <Icon size={15} />
              <span className="hidden sm:inline">{label}</span>
              {activeTab === key && <span className="absolute bottom-0 h-0.5 w-40 max-w-[80%] rounded-full bg-violet-600" />}
            </button>
          ))}
        </div>
      </section>

      {activeTab === 'providers' && (
        <section className="surface-card p-5">
          <h1 className="text-base font-bold text-slate-900">Connected Providers</h1>
          <p className="mt-1 text-xs text-slate-500">Manage your notification API providers and channels.</p>

          <div className="mt-4 overflow-hidden rounded-lg border border-slate-200">
            {providers.map((provider) => {
              const selected = form.provider === provider.id;
              const isConnected = provider.id === 'slek' || (provider.id === 'apiwap' && connected);
              return (
                <div key={provider.id} className={`${provider.accent || ''} border-b border-slate-100 bg-white last:border-b-0`}>
                  <div className="grid gap-4 p-4 lg:grid-cols-[80px_1fr_auto_auto_auto] lg:items-center">
                    <button type="button" onClick={() => setForm((current) => ({ ...current, provider: provider.id }))} className={`flex h-16 w-20 items-center justify-center rounded-md border ${selected ? 'border-violet-200 bg-violet-50/40' : 'border-slate-200 bg-white'}`}>
                      {provider.logo}
                    </button>
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-sm font-bold text-slate-900">{provider.name}</h2>
                        {provider.badge && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-bold text-violet-600">{provider.badge}</span>}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{provider.channel} Provider</p>
                      <p className={`mt-1 text-[11px] font-semibold ${isConnected ? 'text-emerald-600' : 'text-orange-500'}`}>
                        {provider.country && <span className="mr-2 text-slate-600">{provider.country}</span>}
                        {isConnected ? 'Connected' : 'Not configured'}
                      </p>
                    </div>
                    <ProviderBadge provider={provider} />
                    <div className="flex items-center gap-2">
                      {(provider.id === 'slek' || provider.id === 'apiwap') && (
                        <button type="button" className="btn-secondary" onClick={() => testConnection(provider)} disabled={testing || !isConnected}>
                          <MessageSquare size={14} />
                          {testing ? 'Testing...' : 'Test Connection'}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-secondary text-violet-600"
                        onClick={() => {
                          setForm((current) => ({ ...current, provider: provider.id }));
                          if (provider.id === 'apiwap') setConfigOpen(true);
                          if (provider.id === 'slek') saveSettings({ ...form, provider: 'slek', whatsapp_enabled: true });
                        }}
                      >
                        <Settings size={14} />
                        Configure
                      </button>
                    </div>
                    <button type="button" className="rounded-md p-2 text-slate-500 hover:bg-slate-50" aria-label={`${provider.name} menu`}><MoreVertical size={17} /></button>
                  </div>
                </div>
              );
            })}
          </div>

        </section>
      )}

      {activeTab === 'types' && (
        <section className="surface-card p-5">
          <h1 className="text-base font-bold text-slate-900">Notification Types</h1>
          <div className="mt-4 grid gap-3">
            {notificationTypes.map(([key, Icon, title, description]) => (
              <div key={key} className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 p-4">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-violet-50 text-violet-600"><Icon size={17} /></span>
                  <div><h2 className="text-sm font-semibold text-slate-900">{title}</h2><p className="mt-1 text-xs text-slate-500">{description}</p></div>
                </div>
                <Toggle checked={form[key]} onChange={() => toggle(key)} />
              </div>
            ))}
          </div>
        </section>
      )}

      {activeTab === 'templates' && (
        <section className="surface-card p-5">
          <h1 className="text-base font-bold text-slate-900">Message Templates</h1>
          <p className="mt-1 text-xs text-slate-500">Write simple message text. Customer details are added automatically by the system.</p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            {templateFields.map(([name, label, helper]) => (
              <label key={name} className="block rounded-lg border border-slate-200 p-4">
                <span className="text-sm font-semibold text-slate-900">{label}</span>
                <textarea name={name} value={form[name]} onChange={update} className="mt-3 min-h-28 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-xs leading-relaxed outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100" />
                <span className="mt-2 block text-[11px] text-slate-500">{helper}</span>
                <span className="mt-1 block text-[11px] text-slate-400">Characters: {form[name]?.length || 0}</span>
              </label>
            ))}
          </div>
        </section>
      )}

      <div className="flex justify-end">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? <Send size={17} /> : <Save size={17} />}
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      {configOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <div className="w-full max-w-lg rounded-lg bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 className="text-base font-bold text-slate-900">Configure ApiWap</h2>
                <p className="mt-1 text-xs text-slate-500">Paste the API key generated from your ApiWap account.</p>
              </div>
              <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700" onClick={() => setConfigOpen(false)} aria-label="Close configure dialog">
                <X size={18} />
              </button>
            </div>

            <div className="space-y-4 p-5">
              <label className="form-label">ApiWap API key
                <input
                  className="form-input"
                  type="password"
                  name="apiwap_api_key"
                  value={form.apiwap_api_key}
                  onChange={update}
                  placeholder="Paste ApiWap API key"
                  autoComplete="off"
                />
              </label>
              <label className="form-label">ApiWap base URL
                <input className="form-input" name="apiwap_base_url" value={form.apiwap_base_url} onChange={update} />
              </label>
              <p className="flex items-center gap-2 text-xs text-slate-500">
                <CheckCircle2 size={14} className={connected ? 'text-emerald-500' : 'text-slate-400'} />
                {connected ? 'An ApiWap API key is saved for this account.' : 'Generate your ApiWap API key, paste it here, then save.'}
              </p>
            </div>

            <div className="flex flex-col gap-2 border-t border-slate-200 px-5 py-4 sm:flex-row sm:justify-end">
              <a href="https://account.apiwap.com/register" target="_blank" rel="noreferrer" className="btn-secondary justify-center text-violet-600">
                <ExternalLink size={15} />
                Get API Key
              </a>
              <button type="button" className="btn-primary justify-center" onClick={saveApiKey} disabled={saving}>
                <Save size={15} />
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </form>
  );
}
