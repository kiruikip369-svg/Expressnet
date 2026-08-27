import {
  Bold,
  Check,
  FileText,
  ImagePlus,
  Italic,
  MessageSquare,
  Radio,
  RotateCcw,
  Save,
  Underline,
  Users,
  Eye,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import { DEFAULT_TENANT_THEME, getStoredTenantSettings, storeTenantSettings } from '../utils/theme';
import TenantUsers from './users';

const tabs = [
  { key: 'general', label: 'General Settings', icon: Radio },
  { key: 'sms', label: 'SMS', icon: MessageSquare },
  { key: 'users', label: 'Users & Permissions', icon: Users },
];

const initialSettings = {
  companyName: 'EXPRESS PLOT WIFI',
  themeColor: DEFAULT_TENANT_THEME.themeColor,
  themeMode: DEFAULT_TENANT_THEME.themeMode,
  darkMode: false,
  font: DEFAULT_TENANT_THEME.font,
  supportPhone: '+254716632851',
  supportEmail: '',
  requireTerms: false,
  terms: '',
  smsProvider: 'Roamtech',
  smsSenderId: 'EXPRESS WIFI',
  smsTemplate: 'Dear {{name}}, your {{package}} payment of KES {{amount}} is complete.',
  notificationProvider: 'slek',
  smsEnabled: true,
  smsOnPayment: true,
  whatsappEnabled: true,
  whatsappTemplate: 'Hi {{name}}, your {{package}} internet package is active. Amount payable: Ksh {{amount_payable}}. Username: {{username}}, Password: {{password}}.',
};

const colorPresets = ['#fa8200', '#2563eb', '#16a34a', '#dc2626', '#7c3aed', '#0891b2', '#111827', '#f59e0b'];

function fromApi(data) {
  return {
    companyName: data.business_name || initialSettings.companyName,
    themeColor: data.theme_color || initialSettings.themeColor,
    themeMode: data.theme_mode || (data.dark_mode ? 'dark' : 'light'),
    darkMode: (data.theme_mode || (data.dark_mode ? 'dark' : 'light')) === 'dark',
    font: data.font || initialSettings.font,
    supportPhone: data.phone || initialSettings.supportPhone,
    supportEmail: data.support_email || '',
  };
}

function toApi(settings) {
  return {
    business_name: settings.companyName,
    phone: settings.supportPhone,
    support_email: settings.supportEmail,
    theme_color: settings.themeColor,
    theme_mode: settings.themeMode,
    dark_mode: settings.themeMode === 'dark',
    font: settings.font,
  };
}

function SettingsShell({ title, description, children }) {
  return (
    <section className="theme-card overflow-hidden rounded-lg border">
      <div className="theme-card-muted border-b px-5 py-4">
        <h2 className="theme-text text-sm font-semibold">{title}</h2>
        <p className="theme-muted mt-1 text-xs">{description}</p>
      </div>
      <div className="space-y-5 p-5">{children}</div>
    </section>
  );
}

function Field({ label, required, hint, children }) {
  return (
    <label className="block">
      <span className="theme-text text-xs font-semibold">
        {label}{required && <span className="text-[#ff8a00]">*</span>}
      </span>
      <div className="mt-2">{children}</div>
      {hint && <span className="theme-muted mt-2 block text-xs">{hint}</span>}
    </label>
  );
}

function Input(props) {
  return (
    <input
      {...props}
      className="theme-input h-10 w-full rounded-md border px-3 text-xs font-semibold outline-none transition focus:border-[var(--dashboard-color)] focus:ring-2 focus:ring-[var(--dashboard-color)]/20"
    />
  );
}

function Select({ children, ...props }) {
  return (
    <select
      {...props}
      className="theme-input h-10 w-full rounded-md border px-3 text-xs font-semibold outline-none transition focus:border-[var(--dashboard-color)] focus:ring-2 focus:ring-[var(--dashboard-color)]/20"
    >
      {children}
    </select>
  );
}

function Toggle({ checked, label, onChange }) {
  return (
    <label className="theme-text flex items-center gap-3 text-xs font-semibold">
      <input className="h-4 w-4 rounded accent-[var(--dashboard-color)]" type="checkbox" checked={checked} onChange={onChange} />
      {label}
    </label>
  );
}

export default function BusinessSettings() {
  const [activeTab, setActiveTab] = useState('general');
  const [settings, setSettings] = useState(() => {
    return { ...initialSettings, ...getStoredTenantSettings() };
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [logoUploading, setLogoUploading] = useState(false);
  const [dangerConfirm, setDangerConfirm] = useState('');

  useEffect(() => {
    let mounted = true;
    async function loadSettings() {
      try {
        const [{ data }, notifications] = await Promise.all([
          api.get('/settings/business'),
          api.get('/settings/notifications').catch(() => ({ data: null })),
        ]);
        if (mounted) {
          setSettings((current) => ({
            ...current,
            ...fromApi(data),
            ...(notifications.data ? {
              notificationProvider: notifications.data.provider || current.notificationProvider,
              smsEnabled: notifications.data.sms_enabled !== false,
              smsOnPayment: notifications.data.sms_on_payment !== false,
              whatsappEnabled: notifications.data.whatsapp_enabled !== false,
              smsProvider: notifications.data.provider === 'slek' ? 'Slek WhatsApp' : current.smsProvider,
              smsSenderId: notifications.data.roamtech_sender_id || current.smsSenderId,
              smsTemplate: notifications.data.payment_sms_template || current.smsTemplate,
              whatsappTemplate: notifications.data.payment_whatsapp_template || current.whatsappTemplate,
            } : {}),
          }));
        }
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load business settings');
      } finally {
        if (mounted) setLoading(false);
      }
    }
    loadSettings();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    storeTenantSettings(settings);
  }, [settings]);

  const update = (event) => {
    const { checked, name, type, value } = event.target;
    setSettings((current) => ({ ...current, [name]: type === 'checkbox' ? checked : value }));
  };

  const save = async (event) => {
    event?.preventDefault();
    setSaving(true);
    try {
      const [{ data }] = await Promise.all([
        api.patch('/settings/business', toApi(settings)),
        api.patch('/settings/notifications', {
          provider: settings.notificationProvider,
          sms_enabled: settings.smsEnabled,
          sms_on_payment: settings.smsOnPayment,
          whatsapp_enabled: settings.whatsappEnabled,
          roamtech_sender_id: settings.smsSenderId,
          payment_sms_template: settings.smsTemplate,
          payment_whatsapp_template: settings.whatsappTemplate,
        }),
      ]);
      if (data.config) {
        setSettings((current) => ({ ...current, ...fromApi(data.config) }));
      }
      toast.success(data.message || 'Settings saved');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const uploadLogo = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('logo', file);
    setLogoUploading(true);
    try {
      const { data } = await api.post('/settings/logo', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(data.message || 'Logo uploaded');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to upload logo');
    } finally {
      setLogoUploading(false);
    }
  };

  const testSms = async () => {
    try {
      const { data } = await api.post('/settings/test-sms', { phone: settings.supportPhone, channel: 'whatsapp', message: settings.whatsappTemplate });
      toast.success(data.message || 'Test WhatsApp sent');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to send test WhatsApp');
    }
  };

  const deleteCustomers = async () => {
    if (!dangerConfirm.trim()) {
      toast.error('Type your business name to confirm');
      return;
    }
    try {
      const { data } = await api.post('/settings/delete-customers', { confirm: dangerConfirm });
      toast.success(data.message || 'Customers deleted');
      setDangerConfirm('');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Could not delete customers');
    }
  };

  if (loading) {
    return <div className="theme-card rounded-lg border p-4 text-xs">Loading settings...</div>;
  }

  const showFormActions = activeTab !== 'users';

  return (
    <div className="theme-page min-h-[calc(100vh-96px)] rounded-lg p-4 shadow-sm">
      <div className="flex flex-col gap-3 border-b border-[var(--app-border)] pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="theme-text text-xl font-semibold">Settings</h1>
          <p className="theme-muted mt-1 max-w-xl text-xs leading-5">
            Configure your system settings and other preferences to customize your billing system.
          </p>
        </div>
        {showFormActions && (
          <button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90" onClick={save}>
            <Save size={15} />
            {saving ? 'Saving...' : 'Save changes'}
          </button>
        )}
      </div>

      <div className="mt-6 flex gap-5 overflow-x-auto border-b border-[var(--app-border)]">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={`inline-flex h-10 shrink-0 items-center gap-2 border-b-2 px-1 text-xs font-semibold transition ${
              activeTab === key ? 'border-[var(--dashboard-color)] text-[var(--dashboard-color)]' : 'theme-muted border-transparent hover:text-[var(--app-text)]'
            }`}
            onClick={() => setActiveTab(key)}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      <div className="mt-5 space-y-5">
        {activeTab === 'general' && (
          <>
            <SettingsShell title="Appearance" description="Configure your system appearance settings.">
              <Field label="System Logo">

                <label className="theme-card-muted flex h-20 cursor-pointer items-center justify-center rounded-lg border text-xs">
                  <ImagePlus size={16} className="mr-2" />
                  {logoUploading ? 'Uploading...' : <>Drag & Drop your files or <span className="theme-text ml-1 font-semibold">Browse</span></>}
                  <input className="hidden" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={uploadLogo} />
                </label>
                <p className="theme-muted mt-2 text-xs">Upload a Logo that will be used in the header of the system and login page.</p>
              </Field>

              <div className="grid gap-5 lg:grid-cols-2">
                <Field label="The name of your ISP / Wifi Company" required>
                  <Input name="companyName" value={settings.companyName} onChange={update} />
                </Field>
                <Field label="Color" hint="What color should we use for the system?">
                  <div className="flex gap-2">
                    <Input name="themeColor" value={settings.themeColor} onChange={update} />
                    <input className="theme-input h-10 w-12 rounded-md border" type="color" name="themeColor" value={settings.themeColor} onChange={update} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {colorPresets.map((color) => (
                      <button
                        key={color}
                        type="button"
                        aria-label={`Use ${color}`}
                        className={`h-7 w-7 rounded-full border-2 ${settings.themeColor === color ? 'border-slate-900 ring-2 ring-[var(--dashboard-color)]/30' : 'border-white shadow'}`}
                        style={{ backgroundColor: color }}
                        onClick={() => setSettings((current) => ({ ...current, themeColor: color }))}
                      />
                    ))}
                  </div>
                </Field>
                <Field label="Theme">
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      ['light', 'White'],
                      ['dark', 'Dark'],
                      ['system', 'System'],
                    ].map(([mode, label]) => (
                      <button
                        key={mode}
                        type="button"
                        className={`h-10 rounded-md border text-xs font-semibold transition ${settings.themeMode === mode ? 'border-[var(--dashboard-color)] bg-[var(--dashboard-color)] text-white' : 'theme-card border-[var(--app-border)] hover:bg-[var(--app-panel-muted)]'}`}
                        onClick={() => setSettings((current) => ({ ...current, themeMode: mode, darkMode: mode === 'dark' }))}
                      >                                                                                                                             
                        {label}
                      </button>
                    ))}
                  </div>
                </Field>
                <Field label="Font">
                  <Select name="font" value={settings.font} onChange={update}>
                    <option>Work Sans</option>
                    <option>Roboto</option>
                    <option>Inter</option>
                  </Select>
                </Field>
                <Field label="Customer Support Number" required hint="The number your clients can contact when they need support.">
                  <Input name="supportPhone" value={settings.supportPhone} onChange={update} />
                </Field>
                <Field label="Customer Support Email" hint="The email your clients can contact when they need support.">
                  <Input name="supportEmail" value={settings.supportEmail} onChange={update} />
                </Field>
              </div>
            </SettingsShell>

            <SettingsShell title="Terms & Conditions" description="Terms and conditions for your business.">
              <Toggle checked={settings.requireTerms} label="Require users to accept Terms and Conditions" onChange={(event) => setSettings((current) => ({ ...current, requireTerms: event.target.checked }))} />
              <Field label="Terms and Conditions">
                <div className="theme-input rounded-md border">
                  <div className="theme-card-muted flex h-10 items-center gap-4 border-b px-4">
                    {[Bold, Italic, Underline, FileText, RotateCcw, Eye].map((Icon, index) => <Icon key={index} size={15} />)}
                  </div>
                  <textarea
                    name="terms"
                    value={settings.terms}
                    onChange={update}
                    className="min-h-20 w-full resize-y bg-transparent px-3 py-2 text-xs outline-none"
                  />
                </div>
              </Field>
            </SettingsShell>
          </>
        )}

        {activeTab === 'sms' && (
          <SettingsShell title="Communication Settings" description="Configure the default customer messaging channel and templates.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Field label="Default Provider">
                <Select name="notificationProvider" value={settings.notificationProvider} onChange={update}>
                  <option value="slek">Slek WhatsApp</option>
                </Select>
              </Field>
              <Field label="Sender ID"><Input name="smsSenderId" value={settings.smsSenderId} onChange={update} /></Field>
            </div>
            <div className="grid gap-3 lg:grid-cols-3">
              <Toggle checked={settings.whatsappEnabled} label="Enable WhatsApp messages" onChange={(event) => setSettings((current) => ({ ...current, whatsappEnabled: event.target.checked }))} />
              <Toggle checked={settings.smsEnabled} label="Enable SMS fallback" onChange={(event) => setSettings((current) => ({ ...current, smsEnabled: event.target.checked }))} />
              <Toggle checked={settings.smsOnPayment} label="Send on payment" onChange={(event) => setSettings((current) => ({ ...current, smsOnPayment: event.target.checked }))} />
            </div>
            <Field label="Payment WhatsApp Template">
              <textarea name="whatsappTemplate" value={settings.whatsappTemplate} onChange={update} className="theme-input min-h-28 w-full rounded-md border px-3 py-2 text-xs outline-none focus:border-[var(--dashboard-color)]" />
            </Field>
            <Field label="Payment SMS Template">
              <textarea name="smsTemplate" value={settings.smsTemplate} onChange={update} className="theme-input min-h-28 w-full rounded-md border px-3 py-2 text-xs outline-none focus:border-[var(--dashboard-color)]" />
            </Field>
            <button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90" onClick={testSms}>
              Send test WhatsApp
            </button>
          </SettingsShell>
        )}

        {activeTab === 'users' && <TenantUsers embedded />}
      </div>

      {activeTab !== 'users' && (
        <div className="mt-6 rounded-lg border border-red-900/60 bg-red-950/30 p-5">
          <h2 className="text-sm font-semibold text-red-200">Danger Zone</h2>
          <p className="mt-1 text-xs text-red-100/80">Delete all customers and attempt to remove their MikroTik access records. This cannot be undone.</p>
          <div className="mt-4 flex flex-col gap-3 md:flex-row">
            <Input placeholder={`Type ${settings.companyName} to confirm`} value={dangerConfirm} onChange={(event) => setDangerConfirm(event.target.value)} />
            <button type="button" className="h-10 shrink-0 rounded-md bg-red-600 px-4 text-xs font-semibold text-white hover:bg-red-700" onClick={deleteCustomers}>
              Delete all customers
            </button>
          </div>
        </div>
      )}

      {showFormActions && (
        <div className="mt-6 flex gap-3">
          <button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90" onClick={save}>
            {saving ? 'Saving...' : 'Save changes'}
          </button>
          <button type="button" className="theme-card h-10 rounded-md border px-4 text-xs font-semibold" onClick={() => setSettings(initialSettings)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
