import { useEffect, useState } from 'react';
import { BookOpen, PackagePlus, PlugZap, RefreshCw, Router, Search, Sparkles, Wifi } from 'lucide-react';
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

function packageDuration(pkg) {
  if (pkg.duration_label) return pkg.duration_label;
  const unit = pkg.duration_unit || 'days';
  const value = pkg.duration_value || pkg.duration_hours || pkg.duration_days || 1;
  if (unit === 'hours') return `${value} hour${Number(value) === 1 ? '' : 's'}`;
  return `${pkg.duration_days || value} day${Number(pkg.duration_days || value) === 1 ? '' : 's'}`;
}

function packageType(pkg) {
  const value = String(pkg?.service_type || pkg?.package_type || pkg?.type || '').trim().toLowerCase();
  if (['pppoe', 'ppoe', 'ppp', 'broadband'].includes(value)) return 'pppoe';
  return 'hotspot';
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
      ...(name === 'service_type' && value === 'pppoe' ? { duration_unit: 'days' } : {}),
    }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    const nextErrors = {};
    if (!form.name.trim()) nextErrors.name = 'Package name is required';
    if (!form.speed.trim()) nextErrors.speed = 'Speed is required';
    if (!form.duration_value || Number(form.duration_value) <= 0) nextErrors.duration_value = 'Duration must be greater than 0';
    if (form.service_type === 'pppoe' && form.duration_unit === 'hours') nextErrors.duration_value = 'PPPoE packages must use days';
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
      duration_unit: packageType(pkg) === 'pppoe' ? 'days' : pkg.duration_unit || 'days',
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
        name: form.name,
        speed: form.speed,
        duration_value: Number(form.duration_value),
        duration_unit: form.service_type === 'pppoe' ? 'days' : form.duration_unit,
        duration_days: form.service_type !== 'pppoe' && form.duration_unit === 'hours' ? 1 : Number(form.duration_value),
        duration_hours: form.service_type !== 'pppoe' && form.duration_unit === 'hours' ? Number(form.duration_value) : Number(form.duration_value) * 24,
        price: Number(form.price),
        is_active: form.is_active,
      };
      if (!editingPackage || form.service_type !== packageType(editingPackage)) {
        payload.service_type = form.service_type;
      }

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
    if (!window.confirm(`Delete ${pkg.name}? This will remove the router PPP profile if connected.`)) return;

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
      <section className="surface-card">
        <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="page-title">Packages</h1>
            <p className="page-subtitle">Manage internet packages for your clients, pricing, speeds, schedules, and MikroTik profiles.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={applyQuickTemplate}>
              <Sparkles size={17} />
              Quick Templates
            </button>
            <button type="button" className="btn-secondary" onClick={() => toast('Use speed formats like 5M/5M, 10M/10M, or 512K/512K.')}>
              <BookOpen size={17} />
              Package Guide
            </button>
            <button type="button" className="btn-secondary" onClick={syncAllPackages} disabled={syncingAll || packages.length === 0}>
              <RefreshCw size={17} className={syncingAll ? 'animate-spin' : ''} />
              {syncingAll ? 'Syncing...' : 'Sync All'}
            </button>
            <button type="button" className="btn-primary" onClick={openAddModal}>
              <PackagePlus size={17} />
              Create Package
            </button>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            {[
              ['all', 'All'],
              ['hotspot', 'Hotspot'],
              ['pppoe', 'PPPOE'],
              ['free', 'Free Trial'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium ${
                  filter === key ? 'border-app-navy bg-app-navy text-white' : 'border-slate-200 bg-white text-app-navy'
                }`}
                onClick={() => setFilter(key)}
              >
                {label}
                <span className={`rounded px-1.5 text-xs ${filter === key ? 'bg-white text-app-navy' : 'bg-app-navy text-white'}`}>{counts[key]}</span>
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
                <th className="px-4 py-3">Active</th>
                <th className="px-4 py-3">Router</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td className="table-cell text-slate-500" colSpan="8">Loading packages...</td></tr>
              ) : filteredPackages.length === 0 ? (
                <tr><td className="table-cell text-slate-500" colSpan="8">No packages found.</td></tr>
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
                  <td className="table-cell">
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className="btn-secondary" onClick={() => syncPackage(pkg)} disabled={syncingId === pkg.id}>
                        <RefreshCw size={15} className={syncingId === pkg.id ? 'animate-spin' : ''} />
                        {syncingId === pkg.id ? 'Syncing...' : 'Sync Router'}
                      </button>
                      <button type="button" className="btn-secondary" onClick={() => openEditModal(pkg)}>
                        Edit
                      </button>
                      <button type="button" className="btn-danger" onClick={() => deletePackage(pkg)} disabled={deletingId === pkg.id}>
                        {deletingId === pkg.id ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
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
                  <input id="duration_value" name="duration_value" type="number" min="1" step="1" className="form-input" value={form.duration_value} onChange={update} />
                  <select name="duration_unit" className="form-input" value={form.service_type === 'pppoe' ? 'days' : form.duration_unit} onChange={update} disabled={form.service_type === 'pppoe'}>
                    {form.service_type !== 'pppoe' && <option value="hours">Hours</option>}
                    <option value="days">Days</option>
                  </select>
                </div>
                {errors.duration_value && <p className="form-error">{errors.duration_value}</p>}
              </div>
              <div>
                <label className="form-label" htmlFor="price">Price</label>
                <input id="price" name="price" type="number" className="form-input" value={form.price} onChange={update} />
                {errors.price && <p className="form-error">{errors.price}</p>}
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
