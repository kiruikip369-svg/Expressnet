import { CreditCard, Database, Download, Pause, Pencil, PlugZap, Plus, RefreshCw, Router, Search, Trash2, Users, Wifi } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';
import StatusBadge from '../components/StatusBadge';

const initialForm = {
  name: '',
  phone: '',
  location: '',
  username: '',
  password: '',
  technician: '',
  router_serial_number: '',
  mikrotik_router_id: '',
  support: '',
  package_name: '',
  service_type: 'pppoe',
  provision_mikrotik: true,
  grace_period_enabled: false,
  grace_period_value: '',
  grace_period_unit: 'days',
  session_adjustment_enabled: false,
  session_adjustment_value: '',
  session_adjustment_unit: 'hours',
  session_adjustment_direction: 'add',
};

function toDate(value) {
  if (!value) return null;
  if (value._seconds) return new Date(value._seconds * 1000);
  if (value.seconds) return new Date(value.seconds * 1000);
  return new Date(value);
}

function formatDate(value) {
  const date = toDate(value);
  return date && !Number.isNaN(date.valueOf()) ? date.toLocaleDateString() : '-';
}

function serviceTypeOf(customer) {
  return String(customer?.service_type || 'pppoe').toLowerCase();
}

function serviceLabel(serviceType) {
  if (serviceType === 'pppoe') return 'PPPoE';
  if (serviceType === 'hotspot') return 'Hotspot';
  if (serviceType === 'static') return 'Static';
  return 'User';
}

export default function Customers({ initialFilter = 'all', serviceLocked = null, title = 'Users' }) {
  const [customers, setCustomers] = useState([]);
  const [packages, setPackages] = useState([]);
  const [mikrotikRouters, setMikrotikRouters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [payingId, setPayingId] = useState(null);
  const [provisioningId, setProvisioningId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState(initialFilter);
  const [search, setSearch] = useState('');
  const [form, setForm] = useState(initialForm);
  const [errors, setErrors] = useState({});
  const [editingId, setEditingId] = useState(null);
  const isHotspotOnlyPage = serviceLocked === 'hotspot';
  const hideManualAccessActions = serviceLocked === 'pppoe' || serviceLocked === 'hotspot';
  const activeFormService = serviceLocked || form.service_type || 'pppoe';

  const packageMap = useMemo(() => {
    return packages.reduce((map, item) => {
      map[item.name] = item;
      return map;
    }, {});
  }, [packages]);

  const mikrotikRouterMap = useMemo(() => {
    return mikrotikRouters.reduce((map, router) => {
      map[router.id] = router;
      return map;
    }, {});
  }, [mikrotikRouters]);

  const formPackageOptions = useMemo(() => {
    const selectedService = serviceLocked || form.service_type || 'pppoe';
    return packages.filter((pkg) => (pkg.service_type || 'hotspot') === selectedService);
  }, [form.service_type, packages, serviceLocked]);

  const scopedCustomers = useMemo(() => {
    if (!serviceLocked) return customers;
    return customers.filter((customer) => serviceTypeOf(customer) === serviceLocked);
  }, [customers, serviceLocked]);

  const userStats = useMemo(() => {
    const active = scopedCustomers.filter((customer) => customer.status === 'active').length;
    const hotspot = scopedCustomers.filter((customer) => serviceTypeOf(customer) === 'hotspot').length;
    const pppoe = scopedCustomers.filter((customer) => serviceTypeOf(customer) === 'pppoe').length;
    const staticUsers = scopedCustomers.filter((customer) => serviceTypeOf(customer) === 'static').length;
    const paused = scopedCustomers.filter((customer) => ['paused', 'suspended', 'inactive'].includes(String(customer.status || '').toLowerCase())).length;
    const offline = scopedCustomers.filter((customer) => ['offline', 'expired'].includes(String(customer.status || '').toLowerCase())).length;
    return {
      total: scopedCustomers.length,
      active,
      inactive: scopedCustomers.length - active,
      hotspot,
      pppoe,
      static: staticUsers,
      paused,
      offline,
    };
  }, [scopedCustomers]);

  const filteredCustomers = useMemo(() => {
    return scopedCustomers.filter((customer) => {
      const isActive = customer.status === 'active';
      const serviceType = serviceTypeOf(customer);
      if (statusFilter === 'active' && !isActive) return false;
      if (statusFilter === 'inactive' && isActive) return false;
      if (statusFilter === 'hotspot' && serviceType !== 'hotspot') return false;
      if (statusFilter === 'pppoe' && serviceType !== 'pppoe') return false;
      if (statusFilter === 'static' && serviceType !== 'static') return false;
      if (statusFilter === 'paused' && !['paused', 'suspended', 'inactive'].includes(String(customer.status || '').toLowerCase())) return false;
      if (statusFilter === 'offline' && !['offline', 'expired'].includes(String(customer.status || '').toLowerCase())) return false;
      const haystack = [
        customer.name,
        customer.phone,
        isHotspotOnlyPage ? '' : customer.location,
        customer.username,
        customer.package,
        isHotspotOnlyPage ? '' : customer.technician,
      ].join(' ').toLowerCase();
      return haystack.includes(search.toLowerCase());
    });
  }, [isHotspotOnlyPage, scopedCustomers, search, statusFilter]);

  const userFilterTabs = useMemo(() => ([
    ['all', 'All', userStats.total, Users],
    ['hotspot', 'Hotspot', userStats.hotspot, Wifi],
    ['pppoe', 'PPPoE', userStats.pppoe, CreditCard],
    ['static', 'Static', userStats.static, Database],
    ['paused', 'Paused', userStats.paused, Pause],
    ['offline', 'Offline', userStats.offline, PlugZap],
  ].filter(([key]) => !serviceLocked || ['all', serviceLocked, 'paused', 'offline'].includes(key))), [serviceLocked, userStats]);

  async function load() {
    setLoading(true);
    try {
      const [customerRes, packageRes, mikrotikRes] = await Promise.all([
        api.get('/customers?all=1'),
        api.get('/packages?all=1'),
        api.get('/settings/mikrotik'),
      ]);
      setCustomers(Array.isArray(customerRes.data) ? customerRes.data : customerRes.data.results || []);
      setPackages(Array.isArray(packageRes.data) ? packageRes.data : packageRes.data.results || []);
      const linkedRouters = mikrotikRes.data?.linked_routers || {};
      setMikrotikRouters(Object.entries(linkedRouters).map(([id, router]) => ({
        id,
        label: router.identity || router.board_name || router.name || `MikroTik ${id}`,
        host: router.last_seen_ip || mikrotikRes.data?.mikrotik_host || '',
      })));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load customers');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    setStatusFilter(initialFilter);
    setForm((current) => ({ ...current, service_type: serviceLocked || initialForm.service_type }));
  }, [initialFilter, serviceLocked]);

  const update = (event) => {
    const { name, type, checked, value } = event.target;
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
      ...(name === 'service_type' ? { package_name: '' } : {}),
      ...(name === 'mikrotik_router_id' && value ? { provision_mikrotik: true } : {}),
    }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    const nextErrors = {};
    if (!form.name.trim()) nextErrors.name = 'Name is required';
    if (!form.phone.trim()) nextErrors.phone = 'Phone is required';
    if (!form.package_name) nextErrors.package_name = 'Package is required';
    const selectedPackage = packages.find((pkg) => pkg.name === form.package_name);
    const selectedService = serviceLocked || form.service_type || 'pppoe';
    if (selectedPackage && (selectedPackage.service_type || 'hotspot') !== selectedService) nextErrors.package_name = `Select a ${selectedService.toUpperCase()} package`;
    if (selectedService === 'pppoe' && form.grace_period_enabled) {
      const value = Number(form.grace_period_value);
      if (!Number.isFinite(value) || value <= 0) nextErrors.grace_period_value = 'Enter a grace period greater than zero';
    }
    if (selectedService === 'hotspot' && form.session_adjustment_enabled) {
      const value = Number(form.session_adjustment_value);
      if (!Number.isFinite(value) || value <= 0) nextErrors.session_adjustment_value = 'Enter a session adjustment greater than zero';
    }
    if ((form.provision_mikrotik || form.mikrotik_router_id) && mikrotikRouters.length > 0 && !form.mikrotik_router_id) nextErrors.mikrotik_router_id = 'Select the MikroTik for this customer';
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingId(null);
    setForm({ ...initialForm, service_type: serviceLocked || initialForm.service_type });
    setErrors({});
  };

  const customerPayload = () => {
    const serviceType = serviceLocked || form.service_type || 'pppoe';
    const payload = {
      name: form.name,
      phone: form.phone,
      username: form.username,
      mikrotik_router_id: form.mikrotik_router_id,
      package: form.package_name,
      service_type: serviceType,
      provision_mikrotik: serviceType !== 'static' && form.provision_mikrotik,
    };
    if (serviceType === 'pppoe' && form.grace_period_enabled) {
      payload.grace_period_enabled = form.grace_period_enabled;
      payload.grace_period_value = form.grace_period_value;
      payload.grace_period_unit = form.grace_period_unit;
    }
    if (serviceType === 'hotspot' && form.session_adjustment_enabled) {
      payload.session_adjustment_value = form.session_adjustment_value;
      payload.session_adjustment_unit = form.session_adjustment_unit;
      payload.session_adjustment_direction = form.session_adjustment_direction;
    }
    if (!isHotspotOnlyPage) {
      payload.location = form.location;
      payload.technician = form.technician;
      payload.router_serial_number = form.router_serial_number;
      payload.support = form.support;
    }
    if (form.password.trim()) payload.password = form.password;
    return payload;
  };

  const addCustomer = async (event) => {
    event.preventDefault();
    if (!validate()) return;

    setSaving(true);
    try {
      if (editingId) {
        await api.patch(`/customers/${editingId}`, customerPayload());
        toast.success('Customer updated');
      } else {
        await api.post('/customers/add', {
          ...customerPayload(),
          package_name: form.package_name,
        });
        toast.success('Customer added and credentials sent');
      }
      closeModal();
      await load();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to add customer');
    } finally {
      setSaving(false);
    }
  };

  const editCustomer = (customer) => {
    setEditingId(customer.id);
    setForm({
      name: customer.name || '',
      phone: customer.phone || '',
      location: customer.location || '',
      username: customer.username || '',
      password: '',
      technician: customer.technician || '',
      router_serial_number: customer.router_serial_number || '',
      mikrotik_router_id: customer.mikrotik_router_id || '',
      support: customer.support || '',
      package_name: customer.package || '',
      provision_mikrotik: serviceTypeOf(customer) !== 'static',
      service_type: serviceTypeOf(customer),
      grace_period_enabled: Boolean(customer.grace_period_enabled),
      grace_period_value: customer.grace_period_value || '',
      grace_period_unit: customer.grace_period_unit || 'days',
      session_adjustment_enabled: false,
      session_adjustment_value: '',
      session_adjustment_unit: 'hours',
      session_adjustment_direction: 'add',
    });
    setModalOpen(true);
  };

  const renewCustomer = async (customer) => {
    const packageName = window.prompt('Renew with package name', customer.package || packages[0]?.name || '');
    if (!packageName) return;
    const selected = packages.find((pkg) => pkg.name === packageName);
    if (!selected) {
      toast.error('Package not found');
      return;
    }
    try {
      await api.post(`/customers/${customer.id}/renew`, { package_id: selected.id });
      toast.success('Customer renewed');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to renew customer');
    }
  };

  const exportCsv = () => {
    const headers = isHotspotOnlyPage
      ? ['name', 'phone', 'username', 'package', 'service_type', 'mikrotik_router_id', 'status', 'expiry_date']
      : ['name', 'phone', 'location', 'username', 'package', 'service_type', 'technician', 'router_serial_number', 'mikrotik_router_id', 'support', 'status', 'expiry_date'];
    const csv = [headers.join(','), ...filteredCustomers.map((item) => headers.map((key) => JSON.stringify(item[key] ?? '')).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'users.csv';
    link.click();
    URL.revokeObjectURL(url);
  };

  const expiryClass = (value) => {
    const date = toDate(value);
    if (!date || Number.isNaN(date.valueOf())) return 'text-slate-500';
    const days = (date.getTime() - Date.now()) / 86400000;
    if (days < 0) return 'text-red-600 font-semibold';
    if (days <= 7) return 'text-amber-600 font-semibold';
    return 'text-emerald-600 font-semibold';
  };

  const deleteCustomer = async (customer) => {
    if (!window.confirm(`Delete ${customer.name}?`)) return;

    setDeletingId(customer.id);
    try {
      await api.delete(`/customers/${customer.id}`);
      setCustomers((current) => current.filter((item) => item.id !== customer.id));
      toast.success('Customer deleted');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete customer');
    } finally {
      setDeletingId(null);
    }
  };

  const startPayment = async (customer) => {
    const selectedPackage = packageMap[customer.package];
    setPayingId(customer.id);
    try {
      const { data } = await api.post('/payments/pay', {
        customer_id: customer.id,
        customer_name: customer.name,
        phone: customer.phone,
        amount: selectedPackage?.price,
        package_name: customer.package,
        service_type: serviceTypeOf(customer),
      });
      if (data.authorizationUrl) {
        window.open(data.authorizationUrl, '_blank', 'noopener,noreferrer');
      }
      toast.success('M-Pesa prompt sent');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to start M-Pesa payment');
    } finally {
      setPayingId(null);
    }
  };

  const provisionCustomer = async (customer) => {
    setProvisioningId(customer.id);
    try {
      await api.post(`/customers/${customer.id}/provision`);
      toast.success('Customer provisioned on MikroTik');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to provision customer');
    } finally {
      setProvisioningId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-subtitle">
            {serviceLocked
              ? `Manage ${serviceLabel(serviceLocked)} customers, expiry, payments, and MikroTik provisioning.`
              : 'Manage PPPoE, Hotspot, and Static users from one page, with active and inactive filters.'}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={exportCsv}>
            <Download size={15} />
            Export CSV
          </button>
          <button type="button" className="btn-primary" onClick={() => { setEditingId(null); setForm({ ...initialForm, service_type: serviceLocked || 'pppoe' }); setModalOpen(true); }}>
            <Plus size={15} />
            Add User
          </button>
        </div>
      </div>

      <section className="border-b border-slate-200">
        <div className="flex flex-wrap gap-4">
          {userFilterTabs.map(([key, label, count, Icon]) => {
            const active = statusFilter === key;
            return (
              <button
                key={key}
                type="button"
                className={`flex h-10 items-center gap-2 border-b-2 px-0 text-xs font-normal transition ${
                  active ? 'border-[var(--app-accent)] text-[var(--app-accent)]' : 'border-transparent text-slate-500 hover:text-slate-900'
                }`}
                onClick={() => setStatusFilter(key)}
              >
                <Icon size={16} className={active ? 'text-[var(--app-accent)]' : 'text-slate-400'} />
                <span>{label}</span>
                <span className="rounded-md border px-1.5 py-0.5 text-[10px] leading-none" style={{ borderColor: 'var(--app-accent-soft)', background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>{count}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="surface-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <label className="relative block w-full lg:max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <input
              className="form-input mt-0 pl-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={isHotspotOnlyPage ? 'Search name, phone, username, package' : 'Search name, phone, location, username, package'}
            />
          </label>
          <div className="flex gap-4 text-xs text-slate-500">
            <span>Active: <strong className="font-normal text-slate-900">{userStats.active}</strong></span>
            <span>Inactive: <strong className="font-normal text-slate-900">{userStats.inactive}</strong></span>
          </div>
        </div>
      </section>

      <div className="table-shell overflow-x-auto">
        <table className={`${isHotspotOnlyPage ? 'min-w-[920px]' : 'min-w-[1120px]'} divide-y divide-slate-200`}>
          <thead className="table-head">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Phone</th>
              {!isHotspotOnlyPage && <th className="px-3 py-2">Location</th>}
              <th className="px-3 py-2">Username</th>
              <th className="px-3 py-2">Package</th>
              {!isHotspotOnlyPage && <th className="px-3 py-2">Technician</th>}
              <th className="px-3 py-2">MikroTik</th>
              <th className="px-3 py-2">Expiry</th>
              <th className="px-3 py-2">Status</th>
              <th className="sticky right-0 border-l border-slate-200 bg-slate-50 px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr><td className="table-cell text-slate-500" colSpan={isHotspotOnlyPage ? 8 : 10}>Loading customers...</td></tr>
            ) : filteredCustomers.length === 0 ? (
              <tr><td className="table-cell text-slate-500" colSpan={isHotspotOnlyPage ? 8 : 10}>No customers found.</td></tr>
            ) : filteredCustomers.map((customer) => (
              <tr key={customer.id}>
                <td className="table-cell px-3 font-medium text-slate-900">{customer.name}</td>
                <td className="table-cell px-3">{customer.phone}</td>
                {!isHotspotOnlyPage && <td className="table-cell px-3">{customer.location || '-'}</td>}
                <td className="table-cell px-3">{customer.username}</td>
                <td className="table-cell px-3">{customer.package || '-'}</td>
                {!isHotspotOnlyPage && <td className="table-cell px-3">{customer.technician || '-'}</td>}
                <td className="table-cell px-3">
                  <div className="space-y-1">
                    <p className="font-medium text-slate-900">{mikrotikRouterMap[customer.mikrotik_router_id]?.label || customer.mikrotik_router_id || '-'}</p>
                    <StatusBadge status={customer.provisioning_status || 'pending'} />
                  </div>
                </td>
                <td className={`table-cell px-3 ${expiryClass(customer.expiry_date)}`}>{formatDate(customer.expiry_date)}</td>
                <td className="table-cell px-3"><StatusBadge status={customer.status} /></td>
                <td className="table-cell sticky right-0 border-l border-slate-200 bg-white px-3">
                  <div className="flex flex-nowrap gap-2">
                    {!hideManualAccessActions && (
                      <button type="button" className="btn-secondary" onClick={() => provisionCustomer(customer)} disabled={provisioningId === customer.id}>
                        <Router size={16} />
                        {provisioningId === customer.id ? 'Provisioning...' : 'Provision'}
                      </button>
                    )}
                    <button type="button" className="btn-secondary" onClick={() => startPayment(customer)} disabled={payingId === customer.id}>
                      <CreditCard size={16} />
                      {payingId === customer.id ? 'Sending...' : 'Pay'}
                    </button>
                    {!hideManualAccessActions && serviceTypeOf(customer) !== 'pppoe' && (
                      <button type="button" className="btn-secondary" onClick={() => renewCustomer(customer)}>
                        <RefreshCw size={16} />
                        Renew
                      </button>
                    )}
                    <button type="button" className="btn-secondary" onClick={() => editCustomer(customer)}>
                      <Pencil size={16} />
                      Edit
                    </button>
                    <button type="button" className="btn-danger" onClick={() => deleteCustomer(customer)} disabled={deletingId === customer.id}>
                      <Trash2 size={16} />
                      {deletingId === customer.id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modalOpen && (
        <Modal title={editingId ? 'Edit Customer' : 'Add Customer'} onClose={closeModal}>
          <form className="space-y-4" onSubmit={addCustomer}>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="form-label" htmlFor="name">Full name</label>
                <input id="name" name="name" className="form-input" value={form.name} onChange={update} />
                {errors.name && <p className="form-error">{errors.name}</p>}
              </div>
              <div>
                <label className="form-label" htmlFor="phone">Phone</label>
                <input id="phone" name="phone" className="form-input" value={form.phone} onChange={update} />
                {errors.phone && <p className="form-error">{errors.phone}</p>}
              </div>
              {!isHotspotOnlyPage && (
                <div>
                  <label className="form-label" htmlFor="location">Location</label>
                  <input id="location" name="location" className="form-input" value={form.location} onChange={update} />
                </div>
              )}
              {editingId && (
                <>
                  <div>
                    <label className="form-label" htmlFor="username">Username</label>
                    <input id="username" name="username" className="form-input" value={form.username} onChange={update} />
                    {errors.username && <p className="form-error">{errors.username}</p>}
                  </div>
                  <div>
                    <label className="form-label" htmlFor="password">Password</label>
                    <input
                      id="password"
                      name="password"
                      type="text"
                      className="form-input"
                      value={form.password}
                      onChange={update}
                      placeholder="Leave blank to keep current password"
                    />
                    {errors.password && <p className="form-error">{errors.password}</p>}
                  </div>
                </>
              )}
              {!isHotspotOnlyPage && (
                <>
                  <div>
                    <label className="form-label" htmlFor="technician">Technician who attended</label>
                    <input id="technician" name="technician" className="form-input" value={form.technician} onChange={update} />
                  </div>
                  <div>
                    <label className="form-label" htmlFor="router_serial_number">Router serial number</label>
                    <input id="router_serial_number" name="router_serial_number" className="form-input" value={form.router_serial_number} onChange={update} />
                  </div>
                </>
              )}
              <div>
                <label className="form-label" htmlFor="mikrotik_router_id">Create in MikroTik</label>
                <select id="mikrotik_router_id" name="mikrotik_router_id" className="form-input" value={form.mikrotik_router_id} onChange={update}>
                  <option value="">Select installed MikroTik</option>
                  {mikrotikRouters.map((router) => (
                    <option key={router.id} value={router.id}>
                      {router.label}{router.host ? ` - ${router.host}` : ''}
                    </option>
                  ))}
                </select>
                {errors.mikrotik_router_id && <p className="form-error">{errors.mikrotik_router_id}</p>}
              </div>
              {!isHotspotOnlyPage && (
                <div>
                  <label className="form-label" htmlFor="support">Support</label>
                  <input id="support" name="support" className="form-input" value={form.support} onChange={update} />
                </div>
              )}
              {!serviceLocked && (
                <div className="sm:col-span-2">
                  <label className="form-label" htmlFor="service_type">Service type</label>
                  <select id="service_type" name="service_type" className="form-input" value={form.service_type || 'pppoe'} onChange={update}>
                    <option value="pppoe">PPPoE</option>
                    <option value="hotspot">Hotspot</option>
                    <option value="static">Static</option>
                  </select>
                </div>
              )}
              <div className="sm:col-span-2">
                <label className="form-label" htmlFor="package_name">Package</label>
                <select id="package_name" name="package_name" className="form-input" value={form.package_name} onChange={update}>
                  <option value="">Select a package</option>
                  {formPackageOptions.map((pkg) => (
                    <option key={pkg.id} value={pkg.name}>{pkg.name}</option>
                  ))}
                </select>
                {errors.package_name && <p className="form-error">{errors.package_name}</p>}
              </div>
              {activeFormService === 'pppoe' && (
                <div className="sm:col-span-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <label className="flex items-start gap-3 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      name="grace_period_enabled"
                      className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-[var(--app-accent)] focus:ring-[var(--app-focus-ring)]"
                      checked={form.grace_period_enabled}
                      onChange={update}
                    />
                    <span>
                      <span className="block font-semibold text-slate-800">Give this PPPoE customer a free grace period</span>
                      <span className="mt-1 block">The customer can use the selected package until the grace period ends, then payment is required.</span>
                    </span>
                  </label>
                  {form.grace_period_enabled && (
                    <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_160px]">
                      <div>
                        <label className="form-label" htmlFor="grace_period_value">Grace period</label>
                        <input
                          id="grace_period_value"
                          name="grace_period_value"
                          type="number"
                          min="1"
                          step="1"
                          className="form-input"
                          value={form.grace_period_value}
                          onChange={update}
                          placeholder="e.g. 7"
                        />
                        {errors.grace_period_value && <p className="form-error">{errors.grace_period_value}</p>}
                      </div>
                      <div>
                        <label className="form-label" htmlFor="grace_period_unit">Unit</label>
                        <select id="grace_period_unit" name="grace_period_unit" className="form-input" value={form.grace_period_unit} onChange={update}>
                          <option value="hours">Hours</option>
                          <option value="days">Days</option>
                          <option value="weeks">Weeks</option>
                          <option value="months">Months</option>
                        </select>
                      </div>
                    </div>
                  )}
                </div>
              )}
              {activeFormService === 'hotspot' && editingId && (
                <div className="sm:col-span-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <label className="flex items-start gap-3 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      name="session_adjustment_enabled"
                      className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-[var(--app-accent)] focus:ring-[var(--app-focus-ring)]"
                      checked={form.session_adjustment_enabled}
                      onChange={update}
                    />
                    <span>
                      <span className="block font-semibold text-slate-800">Adjust this Hotspot customer session</span>
                      <span className="mt-1 block">Add back lost time or reduce time from the current expiry.</span>
                    </span>
                  </label>
                  {form.session_adjustment_enabled && (
                    <div className="mt-3 grid gap-3 sm:grid-cols-[140px_1fr_160px]">
                      <div>
                        <label className="form-label" htmlFor="session_adjustment_direction">Action</label>
                        <select id="session_adjustment_direction" name="session_adjustment_direction" className="form-input" value={form.session_adjustment_direction} onChange={update}>
                          <option value="add">Add</option>
                          <option value="subtract">Subtract</option>
                        </select>
                      </div>
                      <div>
                        <label className="form-label" htmlFor="session_adjustment_value">Time</label>
                        <input
                          id="session_adjustment_value"
                          name="session_adjustment_value"
                          type="number"
                          min="1"
                          step="1"
                          className="form-input"
                          value={form.session_adjustment_value}
                          onChange={update}
                          placeholder="e.g. 2"
                        />
                        {errors.session_adjustment_value && <p className="form-error">{errors.session_adjustment_value}</p>}
                      </div>
                      <div>
                        <label className="form-label" htmlFor="session_adjustment_unit">Unit</label>
                        <select id="session_adjustment_unit" name="session_adjustment_unit" className="form-input" value={form.session_adjustment_unit} onChange={update}>
                          <option value="minutes">Minutes</option>
                          <option value="hours">Hours</option>
                          <option value="days">Days</option>
                        </select>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <label className="flex items-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 sm:col-span-2">
                <input
                  type="checkbox"
                  name="provision_mikrotik"
                  className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-[var(--app-accent)] focus:ring-[var(--app-focus-ring)]"
                  checked={form.provision_mikrotik}
                  onChange={update}
                />
                <span>
                  <span className="block font-semibold text-slate-800">Also create this customer on MikroTik now</span>
                  <span className="mt-1 block">
                    This creates the customer on MikroTik using the selected service package/profile.
                  </span>
                </span>
              </label>
            </div>

            <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
              <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? (
                  <>
                    <RefreshCw size={16} className="animate-spin" />
                    Saving...
                  </>
                ) : editingId ? 'Update Customer' : 'Save Customer'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
