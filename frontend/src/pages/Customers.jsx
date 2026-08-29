import { ChevronDown, CreditCard, Database, Download, Eye, EyeOff, Pause, Pencil, PlugZap, Plus, RefreshCw, Router, Search, Trash2, Users, Wifi,CircleCheck, CircleX } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
  amount_payable: '',
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

const ACTIONS_MENU_WIDTH = 176; // w-44
const ACTIONS_MENU_ESTIMATED_HEIGHT = 220;

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
  const [staff, setStaff] = useState([]);
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
  const [openActionsId, setOpenActionsId] = useState(null);
  const [visiblePasswords, setVisiblePasswords] = useState({});
  const [showEditPassword, setShowEditPassword] = useState(false);
  const [actionsPosition, setActionsPosition] = useState(null);
  const actionsMenuRef = useRef(null);
  const actionsButtonRefs = useRef({});
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

  const staffMap = useMemo(() => {
    return staff.reduce((map, member) => {
      if (member.id) map[member.id] = member;
      if (member.name) map[member.name] = member;
      if (member.phone) map[member.phone] = member;
      return map;
    }, {});
  }, [staff]);

  const formPackageOptions = useMemo(() => {
    const selectedService = serviceLocked || form.service_type || 'pppoe';
    if (selectedService === 'static') return packages;
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
    ['active','active',userStats.active,CircleX],
    ['inactive','inactive',userStats.inactive,CircleCheck],
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
        label: router.name || router.mikrotik_name || router.router_name || router.label || router.identity || router.board_name || `MikroTik ${id}`,
        host: router.last_seen_ip || mikrotikRes.data?.mikrotik_host || '',
      })));
      try {
        const staffRes = await api.get('/staff?all=1');
        setStaff(Array.isArray(staffRes.data) ? staffRes.data : staffRes.data.results || []);
      } catch {
        setStaff([]);
      }
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

  const closeActionsMenu = () => {
    setOpenActionsId(null);
    setActionsPosition(null);
  };

  useEffect(() => {
    if (!openActionsId) return undefined;

    function handleClickOutside(event) {
      const menuEl = actionsMenuRef.current;
      const buttonEl = actionsButtonRefs.current[openActionsId];
      if (menuEl && menuEl.contains(event.target)) return;
      if (buttonEl && buttonEl.contains(event.target)) return;
      closeActionsMenu();
    }
    function handleKeyDown(event) {
      if (event.key === 'Escape') closeActionsMenu();
    }
    function handleReposition() {
      closeActionsMenu();
    }

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('scroll', handleReposition, true);
    window.addEventListener('resize', handleReposition);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('scroll', handleReposition, true);
      window.removeEventListener('resize', handleReposition);
    };
  }, [openActionsId]);

  const toggleActions = (id) => {
    if (openActionsId === id) {
      closeActionsMenu();
      return;
    }
    const buttonEl = actionsButtonRefs.current[id];
    if (buttonEl) {
      const rect = buttonEl.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const openUpward = spaceBelow < ACTIONS_MENU_ESTIMATED_HEIGHT && rect.top > spaceBelow;
      let left = rect.right - ACTIONS_MENU_WIDTH;
      left = Math.max(8, Math.min(left, window.innerWidth - ACTIONS_MENU_WIDTH - 8));
      const top = openUpward ? rect.top - 4 : rect.bottom + 4;
      setActionsPosition({ top, left, openUpward });
    }
    setOpenActionsId(id);
  };

  const update = (event) => {
    const { name, type, checked, value } = event.target;
    const selectedPackage = name === 'package_name' ? packages.find((pkg) => pkg.name === value) : null;
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
      ...(name === 'service_type' ? { package_name: '', amount_payable: '' } : {}),
      ...(name === 'package_name' && selectedPackage ? { amount_payable: selectedPackage.amount_payable ?? selectedPackage.price ?? '' } : {}),
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
    if (['pppoe', 'static'].includes(selectedService)) {
      const amount = Number(form.amount_payable);
      if (!Number.isFinite(amount) || amount < 0) nextErrors.amount_payable = 'Enter a valid payable amount';
      if (!form.technician) nextErrors.technician = 'Select the technician assigned to this customer';
      if (mikrotikRouters.length > 0 && !form.mikrotik_router_id) nextErrors.mikrotik_router_id = 'Select the MikroTik for this customer';
    }
    if (selectedService === 'pppoe' && form.grace_period_enabled) {
      const value = Number(form.grace_period_value);
      if (!Number.isFinite(value) || value <= 0) nextErrors.grace_period_value = 'Enter a grace period greater than zero';
    }
    if (selectedService === 'hotspot' && form.session_adjustment_enabled) {
      const value = Number(form.session_adjustment_value);
      if (!Number.isFinite(value) || value <= 0) nextErrors.session_adjustment_value = 'Enter a session adjustment greater than zero';
    }
    if (!['pppoe', 'static'].includes(selectedService) && (form.provision_mikrotik || form.mikrotik_router_id) && mikrotikRouters.length > 0 && !form.mikrotik_router_id) nextErrors.mikrotik_router_id = 'Select the MikroTik for this customer';
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingId(null);
    setShowEditPassword(false);
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
    if (['pppoe', 'static'].includes(serviceType)) payload.amount_payable = Number(form.amount_payable || 0);
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
        const { data } = await api.post('/customers/add', {
          ...customerPayload(),
          package_name: form.package_name,
        });
        const whatsappSent = data.notification?.whatsapp?.sent;
        if (whatsappSent) {
          toast.success('Customer added and WhatsApp credentials sent');
        } else if ((serviceLocked || form.service_type) === 'pppoe') {
          toast.success('Customer added');
          toast.error(data.notification?.whatsapp?.error || data.notification?.whatsapp?.skipped || 'WhatsApp credentials were not sent. Check message provider settings.');
        } else {
          toast.success('Customer added');
        }
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
    closeActionsMenu();
    setEditingId(customer.id);
    setShowEditPassword(false);
    setForm({
      name: customer.name || '',
      phone: customer.phone || '',
      location: customer.location || '',
      username: customer.username || '',
      password: customer.password || '',
      amount_payable: customer.amount_payable || '',
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
    closeActionsMenu();
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
      ? ['name', 'phone', 'username', 'password', 'package', 'service_type', 'mikrotik_router_id', 'status', 'expiry_date']
      : ['name', 'phone', 'location', 'username', 'password', 'amount_payable', 'package', 'service_type', 'technician', 'router_serial_number', 'mikrotik_router_id', 'support', 'status', 'expiry_date'];
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
    closeActionsMenu();
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
    closeActionsMenu();
    const selectedPackage = packageMap[customer.package];
    setPayingId(customer.id);
    try {
      const { data } = await api.post('/payments/pay', {
        customer_id: customer.id,
        customer_name: customer.name,
        phone: customer.phone,
        amount: customer.amount_payable || selectedPackage?.amount_payable || selectedPackage?.price,
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
    closeActionsMenu();
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

  const openCustomer = filteredCustomers.find((customer) => customer.id === openActionsId);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-center justify-between gap-2">
        
          <h1 className="tex-[30px]">{title}</h1>
        
       <div className='flex flex-row justify-between gap-3'>
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
        <div className="flex flex-row gap-3 overflow-x-auto">
          {userFilterTabs.map(([key, label, count, Icon]) => {
            const active = statusFilter === key;
            return (
              <button
                key={key}
                type="button"
                className={`flex h-10 shrink-0 items-center gap-2 border-b-2 px-0 text-xs font-normal transition ${
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
          
        </div>
      </section>

      <div className="table-shell overflow-x-auto">
        <table className={`${isHotspotOnlyPage ? 'min-w-[820px]' : 'min-w-[1020px]'} w-full divide-y divide-slate-200`}>
          <thead className="table-head">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Phone</th>
              {!isHotspotOnlyPage && <th className="px-3 py-2">Location</th>}
              <th className="px-3 py-2">Username</th>
              <th className="px-3 py-2">Password</th>
              {!isHotspotOnlyPage && <th className="px-3 py-2">Payable</th>}
              <th className="px-3 py-2">Package</th>
              {!isHotspotOnlyPage && <th className="px-3 py-2">Technician</th>}
              <th className="px-3 py-2">MikroTik</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Expiry</th>
              <th className="px-3 py-2">Status</th>
              <th className="sticky right-0 border-l border-slate-200 bg-slate-50 px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr><td className="table-cell text-slate-500" colSpan={isHotspotOnlyPage ? 10 : 13}>Loading customers...</td></tr>
            ) : filteredCustomers.length === 0 ? (
              <tr><td className="table-cell text-slate-500" colSpan={isHotspotOnlyPage ? 10 : 13}>No customers found.</td></tr>
            ) : filteredCustomers.map((customer) => (
              <tr key={customer.id}>
                <td className="table-cell px-3 font-medium text-slate-900">{customer.name}</td>
                <td className="table-cell px-3">{customer.phone}</td>
                {!isHotspotOnlyPage && <td className="table-cell px-3">{customer.location || '-'}</td>}
                <td className="table-cell px-3">{customer.username}</td>
                <td className="table-cell px-3">
                  <span className="inline-flex items-center gap-2">
                    <span className="min-w-[80px] font-mono text-[11px]">{visiblePasswords[customer.id] ? (customer.password || '-') : customer.password ? '••••••••' : '-'}</span>
                    {customer.password && (
                      <button
                        type="button"
                        className="rounded p-1 text-slate-500 hover:bg-slate-100"
                        onClick={() => setVisiblePasswords((current) => ({ ...current, [customer.id]: !current[customer.id] }))}
                        aria-label={visiblePasswords[customer.id] ? 'Hide password' : 'Show password'}
                      >
                        {visiblePasswords[customer.id] ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    )}
                  </span>
                </td>
                {!isHotspotOnlyPage && <td className="table-cell px-3">Ksh {Number(customer.amount_payable || 0).toLocaleString('en-KE')}</td>}
                <td className="table-cell px-3">{customer.package || '-'}</td>
                {!isHotspotOnlyPage && <td className="table-cell px-3">{staffMap[customer.technician]?.name || customer.technician || '-'}</td>}
                <td className="table-cell px-3">
                 {mikrotikRouterMap[customer.mikrotik_router_id]?.label || customer.mikrotik_router_id || '-'}
                </td>
                <td>{customer.provisioning_status || 'pending'}</td>
                <td className={`table-cell px-3 ${expiryClass(customer.expiry_date)}`}>{formatDate(customer.expiry_date)}</td>
                <td className="table-cell px-3"><StatusBadge status={customer.status} /></td>
                <td className="table-cell px-3">
                  <button
                    type="button"
                    ref={(el) => { actionsButtonRefs.current[customer.id] = el; }}
                    className="btn-secondary"
                    onClick={() => toggleActions(customer.id)}
                    aria-haspopup="true"
                    aria-expanded={openActionsId === customer.id}
                  >
                    <ChevronDown size={16} className={`transition-transform ${openActionsId === customer.id ? 'rotate-180' : ''}`} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {openActionsId && actionsPosition && openCustomer && createPortal(
        <div
          ref={actionsMenuRef}
          className="fixed z-[9999] w-44 origin-top-right rounded-md border border-slate-200 bg-white py-1 shadow-lg"
          style={{
            top: actionsPosition.openUpward ? undefined : actionsPosition.top,
            bottom: actionsPosition.openUpward ? window.innerHeight - actionsPosition.top : undefined,
            left: actionsPosition.left,
          }}
          role="menu"
        >
          {!hideManualAccessActions && (
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              onClick={() => provisionCustomer(openCustomer)}
              disabled={provisioningId === openCustomer.id}
            >
              <Router size={15} />
              {provisioningId === openCustomer.id ? 'Provisioning...' : 'Provision'}
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            onClick={() => startPayment(openCustomer)}
            disabled={payingId === openCustomer.id}
          >
            <CreditCard size={15} />
            {payingId === openCustomer.id ? 'Sending...' : 'Pay'}
          </button>
          {!hideManualAccessActions && serviceTypeOf(openCustomer) !== 'pppoe' && (
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
              onClick={() => renewCustomer(openCustomer)}
            >
              <RefreshCw size={15} />
              Renew
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
            onClick={() => editCustomer(openCustomer)}
          >
            <Pencil size={15} />
            Edit
          </button>
          <div className="my-1 border-t border-slate-100" />
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
            onClick={() => deleteCustomer(openCustomer)}
            disabled={deletingId === openCustomer.id}
          >
            <Trash2 size={15} />
            {deletingId === openCustomer.id ? 'Deleting...' : 'Delete'}
          </button>
        </div>,
        document.body
      )}

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
                    <div className="relative">
                      <input
                        id="password"
                        name="password"
                        type={showEditPassword ? 'text' : 'password'}
                        className="form-input pr-10"
                        value={form.password}
                        onChange={update}
                        placeholder="Leave blank to keep current password"
                      />
                      <button
                        type="button"
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:bg-slate-100"
                        onClick={() => setShowEditPassword((value) => !value)}
                        aria-label={showEditPassword ? 'Hide password' : 'Show password'}
                      >
                        {showEditPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                    {errors.password && <p className="form-error">{errors.password}</p>}
                  </div>
                </>
              )}
              {!isHotspotOnlyPage && (
                <>
                  <div>
                    <label className="form-label" htmlFor="technician">Technician who attended</label>
                    <select id="technician" name="technician" className="form-input" value={form.technician} onChange={update}>
                      <option value="">Select technician</option>
                      {staff.map((member) => (
                        <option key={member.id || member.email || member.phone} value={member.id || member.name || member.phone}>
                          {member.name || member.email || member.phone}{member.phone ? ` - ${member.phone}` : ''}
                        </option>
                      ))}
                    </select>
                    {errors.technician && <p className="form-error">{errors.technician}</p>}
                  </div>
                  <div>
                    <label className="form-label" htmlFor="router_serial_number">Router serial number</label>
                    <input id="router_serial_number" name="router_serial_number" className="form-input" value={form.router_serial_number} onChange={update} />
                  </div>
                </>
              )}
              <div>
                <label className="form-label" htmlFor="mikrotik_router_id">MikroTik</label>
                <select id="mikrotik_router_id" name="mikrotik_router_id" className="form-input" value={form.mikrotik_router_id} onChange={update}>
                  <option value="">Select linked MikroTik</option>
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
              {['pppoe', 'static'].includes(activeFormService) && (
                <div className="sm:col-span-2">
                  <label className="form-label" htmlFor="amount_payable">Amount payable</label>
                  <input
                    id="amount_payable"
                    name="amount_payable"
                    type="number"
                    min="0"
                    step="1"
                    className="form-input"
                    value={form.amount_payable}
                    onChange={update}
                    placeholder="Amount the customer should pay"
                  />
                  {errors.amount_payable && <p className="form-error">{errors.amount_payable}</p>}
                </div>
              )}
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
                      <span className="block font-semibold text-slate-800">Give this PPPoE customer grace period</span>
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
