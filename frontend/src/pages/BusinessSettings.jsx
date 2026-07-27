import {
  Bell,
  Bold,
  CreditCard,
  Eye,
  FileText,
  Globe2,
  ImagePlus,
  Italic,
  MessageSquare,
  PlugZap,
  Radio,
  RotateCcw,
  Save,
  Smartphone,
  Underline,
  Wifi,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import { DEFAULT_TENANT_THEME, getStoredTenantSettings, storeTenantSettings } from '../utils/theme';

const tabs = [
  { key: 'general', label: 'General Settings', icon: Radio },
  { key: 'payments', label: 'Payments', icon: CreditCard },
  { key: 'pppoe', label: 'PPPoE', icon: PlugZap },
  { key: 'hotspot', label: 'Hotspot', icon: Wifi },
  { key: 'sms', label: 'SMS', icon: MessageSquare },
  { key: 'whatsapp', label: 'WhatsApp', icon: Smartphone },
  { key: 'notifications', label: 'Notifications', icon: Bell },
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
  paymentGateway: 'Bank Account',
  businessNumber: '',
  bankCode: '',
  bankName: '',
  bankAccount: '',
  subaccountCode: '',
  subaccountStatus: 'not_created',
  pppoeProfile: 'default-pppoe',
  pppoePool: 'pppoe-pool',
  pppoeDns: '8.8.8.8, 1.1.1.1',
  hotspotServer: 'hotspot1',
  hotspotProfile: 'default-hotspot',
  hotspotTrial: 'Disabled',
  smsProvider: 'Roamtech',
  smsSenderId: 'EXPRESS WIFI',
  smsTemplate: 'Dear {{name}}, your {{package}} payment of KES {{amount}} is complete.',
  whatsappProvider: 'Roamtech WhatsApp',
  whatsappTemplate: 'Hello {{name}}, your internet package {{package}} is active.',
  notifyExpiry: true,
  notifyPayment: true,
  notifyOutage: false,
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
    businessNumber: data.business_number || '',
    bankCode: data.bank_code || '',
    bankName: data.bank_name || '',
    bankAccount: data.bank_account_number || '',
    subaccountCode: data.paystack_subaccount_code || '',
    subaccountStatus: data.paystack_subaccount_status || 'not_created',
  };
}

function notificationsFromApi(data) {
  return {
    whatsappProvider: data.provider || data.notification_provider || initialSettings.whatsappProvider,
    whatsappTemplate: data.payment_whatsapp_template || initialSettings.whatsappTemplate,
    notifyPayment: data.whatsapp_enabled ?? initialSettings.notifyPayment,
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
    business_number: settings.businessNumber,
    bank_code: settings.bankCode,
    bank_name: settings.bankName,
    bank_account_number: settings.bankAccount,
    create_subaccount: true,
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
          api.get('/settings/notifications').catch(() => ({ data: {} })),
        ]);
        if (mounted) {
          setSettings((current) => ({ ...current, ...fromApi(data), ...notificationsFromApi(notifications.data) }));
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
    event.preventDefault();
    setSaving(true);
    try {
      const { data } = await api.patch('/settings/business', toApi(settings));
      const { data: notifications } = await api.patch('/settings/notifications', {
        provider: settings.whatsappProvider,
        whatsapp_enabled: settings.notifyPayment,
        payment_whatsapp_template: settings.whatsappTemplate,
      });
      if (data.config) {
        setSettings((current) => ({ ...current, ...fromApi(data.config), ...notificationsFromApi(notifications.config || notifications) }));
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
      const { data } = await api.post('/settings/test-sms', { phone: settings.supportPhone });
      toast.success(data.message || 'Test SMS queued');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to send test SMS');
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

  return (
    <form className="theme-page min-h-[calc(100vh-96px)] rounded-lg p-4 shadow-sm" onSubmit={save}>
      <div className="flex flex-col gap-3 border-b border-[var(--app-border)] pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="theme-text text-xl font-semibold">Settings</h1>
          <p className="theme-muted mt-1 max-w-xl text-xs leading-5">
            Configure your system settings and other preferences to customize your billing system.
          </p>
        </div>
        <button type="submit" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90">
          <Save size={15} />
          {saving ? 'Saving...' : 'Save changes'}
        </button>
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

        {activeTab === 'pppoe' && (
          <SettingsShell title="PPPoE Settings" description="Default PPPoE profile, pools, DNS, and provisioning preferences.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Field label="Default PPPoE Profile" required><Input name="pppoeProfile" value={settings.pppoeProfile} onChange={update} /></Field>
              <Field label="Address Pool" required><Input name="pppoePool" value={settings.pppoePool} onChange={update} /></Field>
              <Field label="DNS Servers"><Input name="pppoeDns" value={settings.pppoeDns} onChange={update} /></Field>
              <Field label="Provisioning Mode"><Select><option>Create disabled until payment</option><option>Create active immediately</option></Select></Field>
            </div>
          </SettingsShell>
        )}

        {activeTab === 'hotspot' && (
          <SettingsShell title="Hotspot Settings" description="Configure captive portal server, profile, vouchers, and trial access.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Field label="Hotspot Server" required><Input name="hotspotServer" value={settings.hotspotServer} onChange={update} /></Field>
              <Field label="Hotspot User Profile" required><Input name="hotspotProfile" value={settings.hotspotProfile} onChange={update} /></Field>
              <Field label="Trial Access"><Select name="hotspotTrial" value={settings.hotspotTrial} onChange={update}><option>Disabled</option><option>10 minutes</option><option>30 minutes</option></Select></Field>
              <Field label="Voucher Login"><Select><option>Username and password</option><option>PIN only</option></Select></Field>
            </div>
          </SettingsShell>
        )}

        {activeTab === 'sms' && (
          <SettingsShell title="SMS Settings" description="Configure SMS provider and customer message templates.">
            <div className="grid gap-5 lg:grid-cols-2">
              <Field label="SMS Provider"><Input name="smsProvider" value={settings.smsProvider} onChange={update} /></Field>
              <Field label="Sender ID"><Input name="smsSenderId" value={settings.smsSenderId} onChange={update} /></Field>
            </div>
            <Field label="Payment SMS Template">
              <textarea name="smsTemplate" value={settings.smsTemplate} onChange={update} className="theme-input min-h-28 w-full rounded-md border px-3 py-2 text-xs outline-none focus:border-[var(--dashboard-color)]" />
            </Field>
            <button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90" onClick={testSms}>
              Send test SMS
            </button>
          </SettingsShell>
        )}

        {activeTab === 'whatsapp' && (
          <SettingsShell title="WhatsApp Settings" description="Configure WhatsApp provider and templates sent after payment.">
            <Field label="WhatsApp Provider"><Input name="whatsappProvider" value={settings.whatsappProvider} onChange={update} /></Field>
            <Field label="Payment WhatsApp Template">
              <textarea name="whatsappTemplate" value={settings.whatsappTemplate} onChange={update} className="theme-input min-h-28 w-full rounded-md border px-3 py-2 text-xs outline-none focus:border-[var(--dashboard-color)]" />
            </Field>
          </SettingsShell>
        )}

        {activeTab === 'notifications' && (
          <SettingsShell title="Notifications" description="Choose which operational events should notify admins or customers.">
            <div className="space-y-4">
              <Toggle checked={settings.notifyPayment} label="Notify customers after successful payments" onChange={(event) => setSettings((current) => ({ ...current, notifyPayment: event.target.checked }))} />
              <Toggle checked={settings.notifyExpiry} label="Send package expiry reminders" onChange={(event) => setSettings((current) => ({ ...current, notifyExpiry: event.target.checked }))} />
              <Toggle checked={settings.notifyOutage} label="Notify admins when routers or access points go offline" onChange={(event) => setSettings((current) => ({ ...current, notifyOutage: event.target.checked }))} />
            </div>
          </SettingsShell>
        )}
      </div>

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

      <div className="mt-6 flex gap-3">
        <button type="submit" className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90">
          {saving ? 'Saving...' : 'Save changes'}
        </button>
        <button type="button" className="theme-card h-10 rounded-md border px-4 text-xs font-semibold" onClick={() => setSettings(initialSettings)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
