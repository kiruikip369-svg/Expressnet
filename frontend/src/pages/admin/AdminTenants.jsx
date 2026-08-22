import {
  ArrowDownUp,
  Building2,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Download,
  Edit,
  ExternalLink,
  Grid3X3,
  List,
  LockKeyhole,
  MoreHorizontal,
  Plus,
  Power,
  Search,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import adminApi from '../../api/adminAxios';
import Modal from '../../components/Modal';

const emptyForm = {
  business_name: '',
  owner_name: '',
  email: '',
  phone: '',
  password: '',
  mikrotik_host: '',
  mikrotik_user: '',
  mikrotik_pass: '',
  mikrotik_port: '8728',
  status: 'active',
  plan: 'basic',
};

const labels = {
  business_name: 'Business name',
  owner_name: 'Owner name',
  email: 'Email',
  phone: 'Phone',
  password: 'Tenant password',
  mikrotik_host: 'MikroTik host',
  mikrotik_user: 'MikroTik user',
  mikrotik_pass: 'MikroTik password',
  mikrotik_port: 'MikroTik port',
  status: 'Status',
  plan: 'Plan',
};

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '-';
}

function formatKES(value) {
  return `KES ${Number(value || 0).toLocaleString()}`;
}

function getExpiryDays(tenant) {
  const explicit = tenant.subscription?.days_until_expiry;
  if (explicit !== undefined && explicit !== null) return Number(explicit);
  if (!tenant.subscription?.expires_at) return null;
  const diff = new Date(tenant.subscription.expires_at).getTime() - Date.now();
  return Math.ceil(diff / 86400000);
}

function getTenantRevenue(tenant) {
  return tenant.subscription?.amount || tenant.subscription?.price || tenant.monthly_revenue || tenant.revenue || 0;
}

function getUserCount(tenant) {
  return tenant.users_count || tenant.customer_count || tenant.customers_count || tenant.total_users || tenant.users || 0;
}

function getOnlineCount(tenant) {
  return tenant.online_users || tenant.active_sessions || tenant.online_count || 0;
}

function getHealth(tenant) {
  if (tenant.status === 'suspended') return 'Critical';
  if (tenant.status === 'inactive') return 'Offline';
  if (tenant.status === 'pending_setup') return 'Warning';
  const days = getExpiryDays(tenant);
  if (days !== null && days < 0) return 'Critical';
  if (days !== null && days <= 7) return 'Warning';
  return tenant.health || 'Healthy';
}

function tenantInitials(name) {
  return String(name || 'Tenant').split(/\s+/).map((part) => part[0]).join('').slice(0, 2).toUpperCase();
}

function StatCard({ label, value, note, icon: Icon, tone = 'blue' }) {
  const tones = {
    blue: 'bg-blue-100 text-blue-600',
    green: 'bg-emerald-100 text-emerald-600',
    amber: 'bg-amber-100 text-amber-600',
    rose: 'bg-rose-100 text-rose-600',
    orange: 'bg-orange-100 text-orange-600',
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-[0_10px_30px_rgba(15,34,64,0.05)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-extrabold text-[#102347]">{label}</p>
          <p className="mt-2 text-3xl font-extrabold leading-none tracking-normal text-[#102347]">{value}</p>
        </div>
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${tones[tone]}`}>
          <Icon size={21} />
        </div>
      </div>
      <p className={`mt-3 text-[11px] font-semibold ${tone === 'rose' ? 'text-rose-600' : tone === 'amber' || tone === 'orange' ? 'text-orange-600' : 'text-emerald-600'}`}>{note}</p>
    </section>
  );
}

function StatusFilterButton({ active, children, dot, onClick }) {
  return (
    <button
      type="button"
      className={`inline-flex h-9 items-center gap-2 rounded-md border px-3 text-[11px] font-bold transition ${
        active ? 'border-blue-500 bg-white text-blue-600 shadow-[0_0_0_3px_rgba(59,130,246,0.12)]' : 'border-slate-200 bg-white text-[#102347] hover:border-blue-200'
      }`}
      onClick={onClick}
    >
      {dot && <span className={`h-2 w-2 rounded-full ${dot}`} />}
      {children}
    </button>
  );
}

function HealthBadge({ value }) {
  const normalized = String(value || 'Healthy').toLowerCase();
  const tone = normalized === 'critical' ? 'bg-rose-500' : normalized === 'warning' ? 'bg-amber-500' : normalized === 'offline' ? 'bg-slate-400' : 'bg-emerald-500';
  return (
    <span className="inline-flex items-center gap-2 text-[12px] font-bold text-[#102347]">
      <span className={`h-2.5 w-2.5 rounded-full ${tone}`} />
      {value}
    </span>
  );
}

function TenantAvatar({ tenant, index }) {
  const colors = ['bg-blue-600', 'bg-orange-500', 'bg-cyan-500', 'bg-violet-600', 'bg-sky-500', 'bg-red-500', 'bg-slate-300', 'bg-emerald-500', 'bg-indigo-500', 'bg-purple-500'];
  return (
    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[11px] font-extrabold text-white ${colors[index % colors.length]}`}>
      {tenant.logo ? <img src={tenant.logo} alt="" className="h-full w-full rounded-full object-cover" /> : tenantInitials(tenant.business_name)}
    </div>
  );
}

function Field({ name, value, error, onChange, type = 'text', placeholder = '' }) {
  return (
    <div>
      <label className="form-label" htmlFor={name}>{labels[name]}</label>
      <input id={name} name={name} type={type} className="form-input" value={value} onChange={onChange} placeholder={placeholder} />
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}

export default function AdminTenants() {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [updatingId, setUpdatingId] = useState(null);
  const [modalMode, setModalMode] = useState(null);
  const [editingTenant, setEditingTenant] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [errors, setErrors] = useState({});
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);

  async function load() {
    setLoading(true);
    try {
      const { data } = await adminApi.get('/admin/tenants');
      setTenants(Array.isArray(data) ? data : []);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to load tenants');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const stats = useMemo(() => ({
    total: tenants.length,
    active: tenants.filter((tenant) => tenant.status === 'active').length,
    pending: tenants.filter((tenant) => tenant.status === 'pending_setup').length,
    suspended: tenants.filter((tenant) => tenant.status === 'suspended').length,
    expiring: tenants.filter((tenant) => {
      const days = getExpiryDays(tenant);
      return days !== null && days >= 0 && days <= 7;
    }).length,
  }), [tenants]);

  const openCreate = () => {
    setModalMode('create');
    setEditingTenant(null);
    setForm(emptyForm);
    setErrors({});
  };

  const openEdit = (tenant) => {
    setModalMode('edit');
    setEditingTenant(tenant);
    setForm({
      ...emptyForm,
      business_name: tenant.business_name || '',
      owner_name: tenant.owner_name || '',
      email: tenant.email || '',
      phone: tenant.phone || '',
      password: '',
      mikrotik_host: tenant.mikrotik_host || '',
      mikrotik_user: tenant.mikrotik_user || '',
      mikrotik_pass: '',
      mikrotik_port: String(tenant.mikrotik_port || 8728),
      status: tenant.status || 'active',
      plan: tenant.subscription?.plan || 'basic',
    });
    setErrors({});
  };

  const closeModal = () => {
    if (saving) return;
    setModalMode(null);
    setEditingTenant(null);
    setForm(emptyForm);
    setErrors({});
  };

  const update = (event) => {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    const nextErrors = {};
    const createRequired = Object.keys(emptyForm).filter((field) => !['status', 'plan'].includes(field));
    const editRequired = ['business_name', 'owner_name', 'email', 'phone'];
    const required = modalMode === 'create' ? createRequired : editRequired;

    required.forEach((field) => {
      if (!String(form[field] || '').trim()) nextErrors[field] = `${labels[field]} is required`;
    });

    if (modalMode === 'create' && form.password.length < 6) {
      nextErrors.password = 'Password must be at least 6 characters';
    }

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const saveTenant = async (event) => {
    event.preventDefault();
    if (!validate()) return;

    setSaving(true);
    try {
      const payload = {
        ...form,
        mikrotik_port: Number(form.mikrotik_port || 8728),
      };

      if (modalMode === 'edit') {
        delete payload.password;
        ['mikrotik_pass'].forEach((field) => {
          if (!String(payload[field] || '').trim()) delete payload[field];
        });
        await adminApi.patch(`/admin/tenants/${editingTenant.id}`, payload);
        toast.success('Tenant updated');
      } else {
        await adminApi.post('/admin/tenants', payload);
        toast.success('Tenant created');
      }

      closeModal();
      await load();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to save tenant');
    } finally {
      setSaving(false);
    }
  };

  const setStatus = async (tenant, status) => {
    setUpdatingId(tenant.id);
    try {
      if (status === 'suspended') {
        await adminApi.patch(`/admin/tenants/${tenant.id}`, { status: 'suspended' });
      } else {
        await adminApi.patch(`/admin/tenants/${tenant.id}`, { status });
      }
      toast.success(status === 'suspended' ? 'Tenant suspended' : 'Tenant activated');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to update tenant');
    } finally {
      setUpdatingId(null);
    }
  };

  const filteredTenants = useMemo(() => {
    return tenants.filter((tenant) => {
      const text = `${tenant.business_name || ''} ${tenant.owner_name || ''} ${tenant.email || ''}`.toLowerCase();
      if (!text.includes(query.toLowerCase())) return false;
      if (statusFilter === 'all') return true;
      if (statusFilter === 'expiring') return tenant.subscription && Number(tenant.subscription.days_until_expiry) <= 7 && Number(tenant.subscription.days_until_expiry) >= 0;
      return tenant.status === statusFilter;
    });
  }, [tenants, query, statusFilter]);

  const pageSize = 10;
  const pagedTenants = filteredTenants.slice((page - 1) * pageSize, page * pageSize);
  const totalPages = Math.max(1, Math.ceil(filteredTenants.length / pageSize));

  const extendTenant = async (tenant) => {
    const current = tenant.subscription?.expires_at ? new Date(tenant.subscription.expires_at) : new Date();
    current.setDate(current.getDate() + 30);
    try {
      await adminApi.patch(`/admin/tenants/${tenant.id}/subscription`, { expires_at: current.toISOString() });
      toast.success('Subscription extended');
      load();
    } catch (error) {
      toast.error(error.response?.data?.error || 'Failed to extend subscription');
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-extrabold text-[#102347]">Tenants</h1>
          <div className="mt-2 flex items-center gap-2 text-[11px] font-semibold text-slate-400">
            <span>Home</span>
            <span>/</span>
            <span className="text-blue-600">Tenants</span>
          </div>
        </div>
        <button className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-blue-600 px-4 text-xs font-bold text-white shadow-[0_10px_22px_rgba(37,99,235,0.22)] hover:bg-blue-700" type="button" onClick={openCreate}>
          <Plus size={16} />
          Create Tenant
          <ChevronDown size={14} />
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <StatCard label="Total Tenants" value={stats.total.toLocaleString()} note="+ 12 this month" icon={Building2} />
        <StatCard label="Active Tenants" value={stats.active.toLocaleString()} note={`${stats.total ? ((stats.active / stats.total) * 100).toFixed(1) : '0.0'}% of total`} icon={CheckCircle2} tone="green" />
        <StatCard label="Pending Setup" value={stats.pending.toLocaleString()} note="Requires onboarding" icon={Clock3} tone="amber" />
        <StatCard label="Suspended" value={stats.suspended.toLocaleString()} note={`${stats.total ? ((stats.suspended / stats.total) * 100).toFixed(1) : '0.0'}% of total`} icon={LockKeyhole} tone="rose" />
        <StatCard label="Expiring Soon" value={stats.expiring.toLocaleString()} note="Within 7 days" icon={CalendarClock} tone="orange" />
      </div>

      <section className="rounded-lg border border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,34,64,0.05)]">
        <div className="flex flex-col gap-4 border-b border-slate-100 p-4 xl:flex-row xl:items-center xl:justify-between">
          <label className="relative block w-full xl:max-w-[390px]">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-blue-500" />
            <input className="h-11 w-full rounded-md border border-slate-200 bg-white pl-11 pr-3 text-[12px] font-semibold text-[#102347] outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100" placeholder="Search tenants, owner, email or domain..." value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-[11px] font-bold text-[#102347] hover:bg-slate-50">
              <Download size={15} />
              Export
            </button>
            <button type="button" className="flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 text-[#102347] hover:bg-slate-50" aria-label="Export options">
              <ChevronDown size={15} />
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-4 border-b border-slate-100 p-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[11px] font-extrabold text-[#102347]">Status:</span>
            <StatusFilterButton active={statusFilter === 'all'} onClick={() => { setStatusFilter('all'); setPage(1); }}>All Status</StatusFilterButton>
            <StatusFilterButton active={statusFilter === 'active'} dot="bg-emerald-500" onClick={() => { setStatusFilter('active'); setPage(1); }}>Active</StatusFilterButton>
            <StatusFilterButton active={statusFilter === 'pending_setup'} dot="bg-amber-500" onClick={() => { setStatusFilter('pending_setup'); setPage(1); }}>Pending Setup</StatusFilterButton>
            <StatusFilterButton active={statusFilter === 'suspended'} dot="bg-rose-500" onClick={() => { setStatusFilter('suspended'); setPage(1); }}>Suspended</StatusFilterButton>
            <StatusFilterButton active={statusFilter === 'expiring'} dot="bg-orange-500" onClick={() => { setStatusFilter('expiring'); setPage(1); }}>Expiring Soon</StatusFilterButton>
            <StatusFilterButton active={statusFilter === 'inactive'} dot="bg-slate-400" onClick={() => { setStatusFilter('inactive'); setPage(1); }}>Offline</StatusFilterButton>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[11px] font-bold text-slate-500">Sort by</span>
            <button type="button" className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-[11px] font-bold text-[#102347]">
              <ArrowDownUp size={14} />
              Newest First
              <ChevronDown size={14} />
            </button>
            <div className="flex rounded-md border border-slate-200 p-1">
              <button type="button" className="flex h-8 w-8 items-center justify-center rounded bg-blue-600 text-white" aria-label="Grid view"><Grid3X3 size={15} /></button>
              <button type="button" className="flex h-8 w-8 items-center justify-center rounded text-slate-500 hover:bg-slate-50" aria-label="List view"><List size={15} /></button>
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
        <table className="w-full min-w-[1120px] text-left">
          <thead className="bg-slate-50 text-[10px] font-extrabold uppercase text-slate-500">
            <tr>
              <th className="px-4 py-4">Tenant</th>
              <th className="px-4 py-3">Owner</th>
              <th className="px-4 py-3">Users</th>
              <th className="px-4 py-3">Revenue (This Month)</th>
              <th className="px-4 py-3">Health</th>
              <th className="px-4 py-3">Subscription Expires</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {loading ? (
              <tr><td className="px-4 py-6 text-xs text-slate-500" colSpan="8">Loading tenants...</td></tr>
            ) : pagedTenants.length === 0 ? (
              <tr><td className="px-4 py-6 text-xs text-slate-500" colSpan="8">No tenants found.</td></tr>
            ) : pagedTenants.map((tenant, index) => {
              const days = getExpiryDays(tenant);
              const health = getHealth(tenant);
              const revenue = getTenantRevenue(tenant);
              return (
              <tr key={tenant.id} className="hover:bg-slate-50/70">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <TenantAvatar tenant={tenant} index={index} />
                    <div className="min-w-0">
                      <p className="truncate text-[12px] font-extrabold uppercase text-[#102347]">{tenant.business_name || '-'}</p>
                      <a className="inline-flex max-w-[190px] items-center gap-1 truncate text-[11px] font-semibold text-blue-500 hover:text-blue-700" href={`/portal/${tenant.id}`} target="_blank" rel="noreferrer">
                        {tenant.domain || `${String(tenant.business_name || 'tenant').toLowerCase().replace(/\s+/g, '')}.expressnet.com`}
                        <ExternalLink size={11} />
                      </a>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <p className="text-[12px] font-bold text-[#102347]">{tenant.owner_name || '-'}</p>
                  <p className="text-[11px] font-semibold text-slate-500">{tenant.email || tenant.phone || '-'}</p>
                </td>
                <td className="px-4 py-3">
                  <p className="text-[12px] font-extrabold text-[#102347]">{Number(getUserCount(tenant)).toLocaleString()}</p>
                  <p className="text-[11px] font-semibold text-emerald-600">{Number(getOnlineCount(tenant)).toLocaleString()} online</p>
                </td>
                <td className="px-4 py-3">
                  <p className="text-[12px] font-extrabold text-[#102347]">{formatKES(revenue)}</p>
                  <p className={`text-[11px] font-bold ${revenue > 0 ? 'text-emerald-600' : 'text-slate-400'}`}>{revenue > 0 ? '+ 8.2%' : '0%'}</p>
                </td>
                <td className="px-4 py-3"><HealthBadge value={health} /></td>
                <td className="px-4 py-3">
                  <p className="text-[12px] font-extrabold text-[#102347]">{formatDate(tenant.subscription?.expires_at)}</p>
                  <p className={`text-[11px] font-bold ${days !== null && days <= 7 ? 'text-orange-600' : 'text-blue-600'}`}>{days === null ? '-' : days < 0 ? 'Expired' : `${days} days left`}</p>
                </td>
                <td className="px-4 py-3 text-[12px] font-bold text-[#102347]">{formatDate(tenant.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <button className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-[#102347] hover:bg-slate-50" type="button" onClick={() => openEdit(tenant)} aria-label="Edit tenant"><Edit size={15} /></button>
                    <button className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-blue-600 hover:bg-blue-50" type="button" onClick={() => extendTenant(tenant)} aria-label="Extend subscription"><CalendarClock size={15} /></button>
                    {tenant.status !== 'active' ? (
                      <button className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-emerald-600 hover:bg-emerald-50" type="button" onClick={() => setStatus(tenant, 'active')} disabled={updatingId === tenant.id} aria-label="Activate tenant"><ShieldCheck size={15} /></button>
                    ) : (
                      <button className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-rose-600 hover:bg-rose-50" type="button" onClick={() => setStatus(tenant, 'suspended')} disabled={updatingId === tenant.id} aria-label="Suspend tenant"><Power size={15} /></button>
                    )}
                    <button className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-[#102347] hover:bg-slate-50" type="button" aria-label="More actions"><MoreHorizontal size={16} /></button>
                  </div>
                </td>
              </tr>);
            })}
          </tbody>
        </table>
        </div>
        <div className="flex flex-col gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span className="font-semibold">Showing {filteredTenants.length === 0 ? 0 : ((page - 1) * pageSize) + 1} to {Math.min(page * pageSize, filteredTenants.length)} of {filteredTenants.length} tenants</span>
          <div className="flex items-center gap-1">
            <button className="h-9 rounded-md border border-slate-200 px-3 text-[11px] font-bold disabled:opacity-50" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, item) => item + 1).map((item) => (
              <button key={item} className={`h-9 w-9 rounded-md text-[12px] font-extrabold ${page === item ? 'bg-blue-600 text-white' : 'border border-slate-200 text-[#102347]'}`} onClick={() => setPage(item)}>{item}</button>
            ))}
            {totalPages > 6 && <span className="px-2 text-slate-400">...</span>}
            {totalPages > 5 && <button className={`h-9 w-9 rounded-md text-[12px] font-extrabold ${page === totalPages ? 'bg-blue-600 text-white' : 'border border-slate-200 text-[#102347]'}`} onClick={() => setPage(totalPages)}>{totalPages}</button>}
            <button className="h-9 rounded-md border border-slate-200 px-3 text-[11px] font-bold text-blue-600 disabled:opacity-50" disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Next</button>
          </div>
        </div>
      </section>

      {modalMode && (
        <Modal title={modalMode === 'create' ? 'Create Tenant' : `Edit ${editingTenant?.business_name || 'Tenant'}`} onClose={closeModal}>
          <form className="space-y-5" onSubmit={saveTenant}>
            <section>
              <h2 className="mb-3 text-sm font-bold text-slate-900">Business Info</h2>
              <div className="grid gap-4 md:grid-cols-2">
                <Field name="business_name" value={form.business_name} error={errors.business_name} onChange={update} />
                <Field name="owner_name" value={form.owner_name} error={errors.owner_name} onChange={update} />
                <Field name="email" type="email" value={form.email} error={errors.email} onChange={update} />
                <Field name="phone" value={form.phone} error={errors.phone} onChange={update} />
                {modalMode === 'create' && <Field name="password" type="password" value={form.password} error={errors.password} onChange={update} />}
                {modalMode === 'edit' && (
                  <div>
                    <label className="form-label" htmlFor="status">Status</label>
                    <select id="status" name="status" className="form-input" value={form.status} onChange={update}>
                      <option value="active">active</option>
                      <option value="pending_setup">pending_setup</option>
                      <option value="suspended">suspended</option>
                      <option value="inactive">inactive</option>
                    </select>
                  </div>
                )}
                <div>
                  <label className="form-label" htmlFor="plan">Plan</label>
                  <select id="plan" name="plan" className="form-input" value={form.plan} onChange={update}>
                    <option value="basic">Basic</option>
                    <option value="pro">Pro</option>
                    <option value="enterprise">Enterprise</option>
                  </select>
                </div>
              </div>
            </section>

            <section>
              <h2 className="mb-3 text-sm font-bold text-slate-900">MikroTik</h2>
              <div className="grid gap-4 md:grid-cols-2">
                <Field name="mikrotik_host" value={form.mikrotik_host} error={errors.mikrotik_host} onChange={update} />
                <Field name="mikrotik_user" value={form.mikrotik_user} error={errors.mikrotik_user} onChange={update} />
                <Field name="mikrotik_pass" type="password" value={form.mikrotik_pass} error={errors.mikrotik_pass} onChange={update} placeholder={modalMode === 'edit' ? 'Leave blank to keep existing' : ''} />
                <Field name="mikrotik_port" type="number" value={form.mikrotik_port} error={errors.mikrotik_port} onChange={update} />
              </div>
            </section>

            <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
              <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
              <button type="submit" className="inline-flex items-center justify-center rounded-md bg-[#e94560] px-4 py-2 text-xs font-bold text-white hover:bg-[#c73652]" disabled={saving}>
                {saving ? 'Saving...' : modalMode === 'create' ? 'Create Tenant' : 'Update Tenant'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
