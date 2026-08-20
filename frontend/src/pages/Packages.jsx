import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, ChevronDown, ChevronLeft, ChevronRight, Edit2, PackagePlus, PlugZap, RefreshCw, Router, Search, Sparkles, Trash2, Wifi } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';

const initialForm = {
  service_type: 'hotspot',
  name: '',
  speed: '',
  duration_value: '',
  duration_unit: 'hours',
  price: '',
  is_active: true,
};

const MONTH_DURATION_OPTIONS = [
  ['1', '1 Month'],
  ['2', '2 Months'],
  ['3', '3 Months'],
  ['6', '6 Months'],
  ['12', '1 Year'],
];

function packageDuration(pkg) {
  if (pkg.duration_label) return pkg.duration_label;
  const unit = pkg.duration_unit || 'days';
  const value = pkg.duration_value || pkg.duration_hours || pkg.duration_days || 1;
  if (unit === 'hours') return `${value} hour${Number(value) === 1 ? '' : 's'}`;
  if (unit === 'months') return `${value} month${Number(value) === 1 ? '' : 's'}`;
  return `${pkg.duration_days || value} day${Number(pkg.duration_days || value) === 1 ? '' : 's'}`;
}

function packageType(pkg) {
  const value = String(pkg?.service_type || pkg?.package_type || pkg?.type || '').trim().toLowerCase();
  if (['pppoe', 'ppoe', 'ppp', 'broadband'].includes(value)) return 'pppoe';
  return 'hotspot';
}

function amountPayable(pkg) {
  return Number(pkg?.amount_payable ?? pkg?.price ?? 0);
}

const MENU_WIDTH = 176; // w-44
const MENU_MARGIN = 6;

function ActionsMenu({ pkg, onSync, onEdit, onDelete, syncing, deleting }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0, openUp: false });
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  const computeCoords = () => {
    const btn = triggerRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const menuHeight = 132; // approx height for 3 items
    const spaceBelow = window.innerHeight - rect.bottom;
    const openUp = spaceBelow < menuHeight + MENU_MARGIN;

    setCoords({
      top: openUp ? rect.top - MENU_MARGIN : rect.bottom + MENU_MARGIN,
      left: Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8),
      openUp,
    });
  };

  const toggleOpen = () => {
    if (!open) computeCoords();
    setOpen((current) => !current);
  };

  useEffect(() => {
    if (!open) return;

    function handleClickOutside(event) {
      if (
        menuRef.current && !menuRef.current.contains(event.target) &&
        triggerRef.current && !triggerRef.current.contains(event.target)
      ) {
        setOpen(false);
      }
    }
    function handleReposition() {
      computeCoords();
    }

    document.addEventListener('mousedown', handleClickOutside);
    window.addEventListener('scroll', handleReposition, true);
    window.addEventListener('resize', handleReposition);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      window.removeEventListener('scroll', handleReposition, true);
      window.removeEventListener('resize', handleReposition);
    };
  }, [open]);

  const handleAction = (action) => {
    setOpen(false);
    action();
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
        onClick={toggleOpen}
        aria-label="Actions"
      >
        <ChevronDown size={16} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && createPortal(
        <div
          ref={menuRef}
          className="fixed z-[9999] w-44 rounded-md border border-slate-200 bg-white py-1 shadow-lg"
          style={{
            top: coords.openUp ? undefined : coords.top,
            bottom: coords.openUp ? window.innerHeight - coords.top : undefined,
            left: coords.left,
          }}
        >
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            onClick={() => handleAction(() => onSync(pkg))}
            disabled={syncing}
          >
            <RefreshCw size={15} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing...' : 'Sync Router'}
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
            onClick={() => handleAction(() => onEdit(pkg))}
          >
            <Edit2 size={15} />
            Edit
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
            onClick={() => handleAction(() => onDelete(pkg))}
            disabled={deleting}
          >
            <Trash2 size={15} />
            {deleting ? 'Deleting...' : 'Delete'}
          </button>
        </div>,
        document.body
      )}
    </>
  );
}

function ScrollableActionBar({ children }) {
  const scrollRef = useRef(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollState = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 4);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  };

  useEffect(() => {
    updateScrollState();
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener('scroll', updateScrollState);
    window.addEventListener('resize', updateScrollState);
    return () => {
      el.removeEventListener('scroll', updateScrollState);
      window.removeEventListener('resize', updateScrollState);
    };
  }, []);

  const scrollBy = (amount) => {
    scrollRef.current?.scrollBy({ left: amount, behavior: 'smooth' });
  };

  return (
    <div className="relative flex items-center">
      {canScrollLeft && (
        <button
          type="button"
          className="absolute left-0 z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm sm:hidden"
          onClick={() => scrollBy(-140)}
          aria-label="Scroll left"
        >
          <ChevronLeft size={16} />
        </button>
      )}

      <div
        ref={scrollRef}
        className="flex flex-nowrap items-center gap-1.5 overflow-x-auto scroll-smooth px-1 pb-1 sm:gap-2 [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]"
      >
        {children}
      </div>

      {canScrollRight && (
        <button
          type="button"
          className="absolute right-0 z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm sm:hidden"
          onClick={() => scrollBy(140)}
          aria-label="Scroll right"
        >
          <ChevronRight size={16} />
        </button>
      )}
    </div>
  );
}

export default function Packages() {
  const [packages, setPackages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [syncingId, setSyncingId] = useState(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPackage, setEditingPackage] = useState(null);
  const [form, setForm] = useState(initialForm);
  const [errors, setErrors] = useState({});
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');

  async function load() {
    setLoading(true);
    try {
      const { data } = await api.get('/packages?all=1');
      setPackages(Array.isArray(data) ? data : data.results || []);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load packages');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const update = (event) => {
    const { checked, name, type, value } = event.target;
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
      ...(name === 'service_type' && value === 'pppoe' && current.duration_unit === 'hours' ? { duration_unit: 'months' } : {}),
    }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    const nextErrors = {};
    if (!form.name.trim()) nextErrors.name = 'Package name is required';
    if (!form.speed.trim()) nextErrors.speed = 'Speed is required';
    if (!form.duration_value || Number(form.duration_value) <= 0) nextErrors.duration_value = 'Duration must be greater than 0';
    if (form.service_type === 'pppoe' && form.duration_unit === 'hours') nextErrors.duration_value = 'PPPoE packages must use days or months';
    if (!form.price || Number(form.price) <= 0) nextErrors.price = 'Price must be greater than 0';
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingPackage(null);
    setForm(initialForm);
    setErrors({});
  };

  const openAddModal = () => {
    setEditingPackage(null);
    setForm(initialForm);
    setErrors({});
    setModalOpen(true);
  };

  const applyQuickTemplate = () => {
    setEditingPackage(null);
    setForm({
      service_type: 'hotspot',
      name: 'Unlimited 24 Hours',
      speed: '5M/5M',
      duration_value: '24',
      duration_unit: 'hours',
      price: '40',
      is_active: true,
    });
    setErrors({});
    setModalOpen(true);
  };

  const openEditModal = (pkg) => {
    setEditingPackage(pkg);
    setForm({
      service_type: packageType(pkg),
      name: pkg.name || '',
      speed: pkg.speed || '',
      duration_value: String(pkg.duration_value || (pkg.duration_unit === 'hours' ? pkg.duration_hours : pkg.duration_days) || ''),
      duration_unit: packageType(pkg) === 'pppoe' && pkg.duration_unit === 'hours' ? 'months' : pkg.duration_unit || 'days',
      price: String(pkg.price || ''),
      is_active: pkg.is_active !== false,
    });
    setErrors({});
    setModalOpen(true);
  };

  const savePackage = async (event) => {
    event.preventDefault();
    if (!validate()) return;

    setSaving(true);
    try {
      const payload = {
        service_type: form.service_type,
        name: form.name,
        speed: form.speed,
        duration_value: Number(form.duration_value),
        duration_unit: form.duration_unit,
        duration_days: form.duration_unit === 'hours' ? 1 : form.duration_unit === 'months' ? undefined : Number(form.duration_value),
        duration_hours: form.duration_unit === 'hours' ? Number(form.duration_value) : form.duration_unit === 'months' ? undefined : Number(form.duration_value) * 24,
        duration_months: form.duration_unit === 'months' ? Number(form.duration_value) : undefined,
        price: Number(form.price),
        amount_payable: Number(form.price),
        is_active: form.is_active,
      };

      if (editingPackage) {
        await api.patch(`/packages/${editingPackage.id}`, payload);
        toast.success('Package updated');
      } else {
        await api.post('/packages/add', payload);
        toast.success('Package added');
      }

      closeModal();
      await load();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save package');
    } finally {
      setSaving(false);
    }
  };

  const deletePackage = async (pkg) => {
    if (!window.confirm(`Delete ${pkg.name}? This will remove the matching router profile if connected.`)) return;

    setDeletingId(pkg.id);
    try {
      await api.delete(`/packages/${pkg.id}`);
      setPackages((current) => current.filter((item) => item.id !== pkg.id));
      toast.success('Package deleted');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete package');
    } finally {
      setDeletingId(null);
    }
  };

  const togglePackage = async (pkg) => {
    try {
      await api.patch(`/packages/${pkg.id}`, { is_active: pkg.is_active === false });
      toast.success(pkg.is_active === false ? 'Package enabled' : 'Package disabled');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to update package');
    }
  };

  const syncPackage = async (pkg) => {
    setSyncingId(pkg.id);
    try {
      const { data } = await api.post(`/packages/${pkg.id}/sync`);
      if (data?.success === false) {
        toast.error(data?.message || 'Failed to sync package profile');
      } else if (data?.queued) {
        toast(
          data?.message || 'Package sync queued — the router applies it on its next check-in (usually within 30s).',
          { icon: '⏳' }
        );
      } else {
        toast.success(data?.message || 'Package profile synced to MikroTik');
      }
      if (data?.package) {
        setPackages((current) => current.map((item) => (item.id === data.package.id ? { ...item, ...data.package } : item)));
      } else {
        setPackages((current) => current.map((item) => (item.id === pkg.id ? { ...item, ppp_profile_status: data?.queued ? 'queued' : 'synced' } : item)));
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to sync package profiles');
    } finally {
      setSyncingId(null);
    }
  };

  const syncAllPackages = async () => {
    setSyncingAll(true);
    try {
      const { data } = await api.post('/packages/sync-all');
      if (data?.success === false && !data?.queued) {
        toast.error(data?.message || 'Failed to sync package profiles');
      } else if (data?.queued) {
        toast(data?.message || 'All package sync queued');
      } else {
        toast.success(data?.message || 'All package profiles synced');
      }
      if (Array.isArray(data?.packages)) {
        const updatedById = new Map(data.packages.map((item) => [item.id, item]));
        setPackages((current) => current.map((item) => (updatedById.has(item.id) ? { ...item, ...updatedById.get(item.id) } : item)));
      } else {
        setPackages((current) => current.map((item) => ({ ...item, ppp_profile_status: data?.queued ? 'queued' : 'synced' })));
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to sync package profiles');
    } finally {
      setSyncingAll(false);
    }
  };

  const filteredPackages = packages.filter((pkg) => {
    const text = `${pkg.name || ''} ${pkg.speed || ''}`.toLowerCase();
    const matchesSearch = text.includes(search.toLowerCase());
    if (!matchesSearch) return false;
    if (filter === 'free') return text.includes('free') || Number(pkg.price || 0) === 0;
    if (filter === 'pppoe') return packageType(pkg) === 'pppoe';
    if (filter === 'hotspot') return packageType(pkg) === 'hotspot';
    return true;
  });

  const counts = {
    all: packages.length,
    hotspot: packages.filter((pkg) => packageType(pkg) === 'hotspot').length,
    pppoe: packages.filter((pkg) => packageType(pkg) === 'pppoe').length,
    free: packages.filter((pkg) => Number(pkg.price || 0) === 0 || `${pkg.name || ''}`.toLowerCase().includes('free')).length,
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Packages</h1>
      </div>

      <ScrollableActionBar>
        <button
          type="button"
          className="btn-secondary shrink-0 whitespace-nowrap px-2 py-1.5 text-[11px] leading-tight sm:px-3 sm:py-2 sm:text-sm"
          onClick={applyQuickTemplate}
        >
          <Sparkles size={14} className="shrink-0 sm:hidden" />
          <Sparkles size={17} className="hidden shrink-0 sm:inline" />
          Quick Templates
        </button>
        <button
          type="button"
          className="btn-secondary shrink-0 whitespace-nowrap px-2 py-1.5 text-[11px] leading-tight sm:px-3 sm:py-2 sm:text-sm"
          onClick={() => toast('Use speed formats like 5M/5M, 10M/10M, or 512K/512K.')}
        >
          <BookOpen size={14} className="shrink-0 sm:hidden" />
          <BookOpen size={17} className="hidden shrink-0 sm:inline" />
          Package Guide
        </button>
        <button
          type="button"
          className="btn-secondary shrink-0 whitespace-nowrap px-2 py-1.5 text-[11px] leading-tight sm:px-3 sm:py-2 sm:text-sm"
          onClick={syncAllPackages}
          disabled={syncingAll || packages.length === 0}
        >
          <RefreshCw size={14} className={`shrink-0 sm:hidden ${syncingAll ? 'animate-spin' : ''}`} />
          <RefreshCw size={17} className={`hidden shrink-0 sm:inline ${syncingAll ? 'animate-spin' : ''}`} />
          {syncingAll ? 'Syncing...' : 'Sync All'}
        </button>
        <button
          type="button"
          className="btn-primary shrink-0 whitespace-nowrap px-2 py-1.5 text-[11px] leading-tight sm:px-3 sm:py-2 sm:text-sm"
          onClick={openAddModal}
        >
          <PackagePlus size={14} className="shrink-0 sm:hidden" />
          <PackagePlus size={17} className="hidden shrink-0 sm:inline" />
          Create Package
        </button>
      </ScrollableActionBar>

      <section className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-nowrap items-center gap-3 overflow-x-auto sm:gap-4">
            {[
              ['all', 'All'],
              ['hotspot', 'Hotspot'],
              ['pppoe', 'PPPOE'],
              ['free', 'Free Trial'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`shrink-0 whitespace-nowrap border-b-2 pb-1 text-xs font-medium transition-colors sm:text-sm ${
                  filter === key
                    ? 'border-app-navy text-app-navy'
                    : 'border-transparent text-slate-500 hover:text-app-navy'
                }`}
                onClick={() => setFilter(key)}
              >
                {label}
                <span className="ml-1 text-[10px] text-slate-400 sm:text-xs">({counts[key]})</span>
              </button>
            ))}
          </div>
          <label className="relative block w-full lg:w-72">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} />
            <input className="form-input pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search" />
          </label>
        </div>
        <div className="table-shell overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="table-head">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Speed</th>
                <th className="px-4 py-3">Duration</th>
                <th className="px-4 py-3">Price</th>
                <th className="px-4 py-3">Amount Payable</th>
                <th className="px-4 py-3">Active</th>
                <th className="px-4 py-3">Router</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td className="table-cell text-slate-500" colSpan="9">Loading packages...</td></tr>
              ) : filteredPackages.length === 0 ? (
                <tr><td className="table-cell text-slate-500" colSpan="9">No packages found.</td></tr>
              ) : filteredPackages.map((pkg, index) => (
                <tr key={pkg.id} className={index % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                  <td className="table-cell font-medium text-slate-950">{pkg.name}</td>
                  <td className="table-cell">
                    <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold uppercase text-slate-700">
                      {packageType(pkg) === 'pppoe' ? <PlugZap size={13} /> : <Wifi size={13} />}
                      {packageType(pkg)}
                    </span>
                  </td>
                  <td className="table-cell">{pkg.speed}</td>
                  <td className="table-cell">{packageDuration(pkg)}</td>
                  <td className="table-cell font-medium text-slate-950">KES {pkg.price}</td>
                  <td className="table-cell font-semibold text-slate-950">KES {amountPayable(pkg).toLocaleString('en-KE')}</td>
                  <td className="table-cell">
                    <button type="button" className={`rounded-full px-2 py-1 text-xs font-semibold ${pkg.is_active === false ? 'bg-slate-100 text-slate-500' : 'bg-emerald-100 text-emerald-700'}`} onClick={() => togglePackage(pkg)}>
                      {pkg.is_active === false ? 'Disabled' : 'Enabled'}
                    </button>
                  </td>
                  <td className="table-cell">
                    <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">
                      <Router size={13} />
                      {pkg.ppp_profile_status || 'pending'}
                    </span>
                  </td>
                  <td className="table-cell text-right">
                    <ActionsMenu
                      pkg={pkg}
                      onSync={syncPackage}
                      onEdit={openEditModal}
                      onDelete={deletePackage}
                      syncing={syncingId === pkg.id}
                      deleting={deletingId === pkg.id}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {modalOpen && (
        <Modal title={editingPackage ? 'Edit Package' : 'Add Package'} onClose={closeModal}>
          <form className="space-y-4" onSubmit={savePackage}>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="form-label" htmlFor="service_type">Package type</label>
                <div className="grid gap-2 sm:grid-cols-2">
                  {[
                    ['hotspot', Wifi, 'Hotspot'],
                    ['pppoe', PlugZap, 'PPPoE'],
                  ].map(([key, Icon, label]) => (
                    <label key={key} className={`flex cursor-pointer items-center gap-3 rounded-md border p-3 text-sm font-semibold ${form.service_type === key ? 'border-app-navy bg-app-navy text-white' : 'border-slate-200 bg-white text-slate-700'}`}>
                      <input className="sr-only" type="radio" name="service_type" value={key} checked={form.service_type === key} onChange={update} />
                      <Icon size={18} />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className="form-label" htmlFor="name">Name</label>
                <input id="name" name="name" className="form-input" value={form.name} onChange={update} />
                {errors.name && <p className="form-error">{errors.name}</p>}
              </div>
              <div>
                <label className="form-label" htmlFor="speed">Speed</label>
                <input id="speed" name="speed" className="form-input" value={form.speed} onChange={update} placeholder="10M or 10M/10M" />
                {errors.speed && <p className="form-error">{errors.speed}</p>}
              </div>
              <div>
                <label className="form-label" htmlFor="duration_value">Duration</label>
                <div className="grid grid-cols-[1fr_auto] gap-2">
                  {form.duration_unit === 'months' ? (
                    <select id="duration_value" name="duration_value" className="form-input" value={form.duration_value} onChange={update}>
                      <option value="">Select months</option>
                      {MONTH_DURATION_OPTIONS.map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  ) : (
                    <input id="duration_value" name="duration_value" type="number" min="1" step="1" className="form-input" value={form.duration_value} onChange={update} />
                  )}
                  <select name="duration_unit" className="form-input" value={form.duration_unit} onChange={update}>
                    {form.service_type !== 'pppoe' && <option value="hours">Hours</option>}
                    <option value="days">Days</option>
                    <option value="months">Months</option>
                  </select>
                </div>
                {form.duration_unit === 'months' && (
                  <p className="mt-1 text-xs text-slate-500">Calendar months are used, so expiry follows the real month length.</p>
                )}
                {errors.duration_value && <p className="form-error">{errors.duration_value}</p>}
              </div>
              <div>
                <label className="form-label" htmlFor="price">Price</label>
                <input id="price" name="price" type="number" className="form-input" value={form.price} onChange={update} />
                {errors.price && <p className="form-error">{errors.price}</p>}
              </div>
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase text-slate-500">Amount payable</p>
                <p className="mt-1 text-base font-bold text-slate-950">KES {Number(form.price || 0).toLocaleString('en-KE')}</p>
              </div>
              <label className="flex items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 sm:col-span-2">
                <input type="checkbox" name="is_active" checked={form.is_active} onChange={update} />
                Package is active and visible on public portal
              </label>
            </div>

            <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
              <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? 'Saving...' : editingPackage ? 'Update Package' : 'Save Package'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}