import { Copy, Plus, Search, Trash2, WalletCards, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';
import StatusBadge from '../components/StatusBadge';

function packageType(pkg) {
  return String(pkg?.service_type || pkg?.package_type || pkg?.type || 'hotspot').trim().toLowerCase();
}

export default function Vouchers() {
  const [vouchers, setVouchers] = useState([]);
  const [packages, setPackages] = useState([]);
  const [packageId, setPackageId] = useState('');
  const [query, setQuery] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);

  const load = async () => {
    try {
      const [vouchersResponse, packagesResponse] = await Promise.all([api.get('/vouchers'), api.get('/packages')]);
      const voucherData = vouchersResponse.data;
      const packageData = packagesResponse.data;
      setVouchers(Array.isArray(voucherData) ? voucherData : voucherData?.results || []);
      setPackages((Array.isArray(packageData) ? packageData : packageData?.results || []).filter((item) => packageType(item) === 'hotspot'));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load vouchers');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(
    () => vouchers.filter((item) => `${item.code} ${item.package} ${item.username}`.toLowerCase().includes(query.toLowerCase())),
    [query, vouchers]
  );
  const filteredIds = useMemo(() => filtered.map((voucher) => voucher.id).filter(Boolean), [filtered]);
  const allFilteredSelected = filteredIds.length > 0 && filteredIds.every((id) => selectedIds.includes(id));

  const createVoucher = async (event) => {
    event.preventDefault();
    if (!packageId) return toast.error('Select a Hotspot package');
    setCreating(true);
    try {
      const { data } = await api.post('/vouchers', { package_id: packageId });
      setVouchers((current) => [data.voucher, ...current]);
      setPackageId('');
      setModalOpen(false);
      toast.success(`Voucher ${data.voucher.code} created`);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to create voucher');
    } finally {
      setCreating(false);
    }
  };

  const expireVoucher = async (voucher) => {
    if (!voucher?.id) return toast.error('This voucher is missing its database id. Refresh the page and try again.');
    if (voucher.status === 'expired') return;
    setBusyId(voucher.id);
    try {
      const { data } = await api.patch(`/vouchers/${voucher.id}`, { status: 'expired' });
      setVouchers((current) => current.map((item) => (item.id === voucher.id ? { ...item, ...data.voucher } : item)));
      toast.success('Voucher expired');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to expire voucher');
    } finally {
      setBusyId('');
    }
  };

  const deleteVoucher = async (voucher) => {
    if (!voucher?.id) return toast.error('This voucher is missing its database id. Refresh the page and try again.');
    if (!window.confirm(`Delete voucher ${voucher.code}?`)) return;
    setBusyId(voucher.id);
    try {
      const { data } = await api.delete(`/vouchers/${voucher.id}`);
      setVouchers((current) => current.filter((item) => item.id !== voucher.id));
      setSelectedIds((current) => current.filter((id) => id !== voucher.id));
      if (data?.router_status === 'queued') {
        toast.success('Voucher deleted. Router removal queued.');
      } else {
        toast.success('Voucher deleted');
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete voucher');
    } finally {
      setBusyId('');
    }
  };

  const toggleVoucherSelection = (voucherId) => {
    setSelectedIds((current) => (
      current.includes(voucherId) ? current.filter((id) => id !== voucherId) : [...current, voucherId]
    ));
  };

  const toggleAllFiltered = () => {
    setSelectedIds((current) => {
      if (allFilteredSelected) return current.filter((id) => !filteredIds.includes(id));
      return Array.from(new Set([...current, ...filteredIds]));
    });
  };

  const deleteSelectedVouchers = async () => {
    const idsToDelete = selectedIds.filter((id) => vouchers.some((voucher) => voucher.id === id));
    if (idsToDelete.length === 0) return;
    if (!window.confirm(`Delete ${idsToDelete.length} selected voucher${idsToDelete.length === 1 ? '' : 's'}?`)) return;
    setBusyId('bulk-delete');
    try {
      const results = await Promise.allSettled(idsToDelete.map((id) => api.delete(`/vouchers/${id}`)));
      const deletedIds = idsToDelete.filter((_, index) => results[index].status === 'fulfilled');
      const failedCount = idsToDelete.length - deletedIds.length;
      setVouchers((current) => current.filter((item) => !deletedIds.includes(item.id)));
      setSelectedIds((current) => current.filter((id) => !deletedIds.includes(id)));
      const queuedCount = results.filter((result) => result.status === 'fulfilled' && result.value?.data?.router_status === 'queued').length;
      if (failedCount) {
        toast.error(`${failedCount} voucher${failedCount === 1 ? '' : 's'} could not be deleted`);
        await load();
      } else if (queuedCount) {
        toast.success(`${deletedIds.length} voucher${deletedIds.length === 1 ? '' : 's'} deleted. Router removal queued.`);
      } else {
        toast.success(`${deletedIds.length} voucher${deletedIds.length === 1 ? '' : 's'} deleted`);
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete selected vouchers');
      await load();
    } finally {
      setBusyId('');
    }
  };

  const copyCode = (code) => {
    navigator.clipboard?.writeText(code);
    toast.success('Voucher code copied');
  };

  return (
    <div className="space-y-4">
      <section className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Vouchers</h1>
          <p className="page-subtitle">Generate Hotspot vouchers and provision them on MikroTik.</p>
        </div>
        <button type="button" className="btn-primary h-9 px-4" onClick={() => setModalOpen(true)}>
          <Plus size={16} />
          Create voucher
        </button>
      </section>

      <section className="surface-card">
        <div className="flex flex-col gap-3 border-b border-slate-200 p-4 lg:flex-row lg:items-center lg:justify-between">
          <label className="relative block w-full max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <input className="form-input mt-0 pl-9" placeholder="Search vouchers" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            {selectedIds.length > 0 && (
              <button type="button" className="btn-danger" onClick={deleteSelectedVouchers} disabled={busyId === 'bulk-delete'}>
                <Trash2 size={14} />
                {busyId === 'bulk-delete' ? 'Deleting...' : `Delete selected (${selectedIds.length})`}
              </button>
            )}
            <span className="text-xs text-slate-500">{filtered.length} vouchers</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[940px] divide-y divide-slate-200">
            <thead className="table-head">
              <tr>
                <th className="px-4 py-3">
                  <input
                    type="checkbox"
                    checked={allFilteredSelected}
                    onChange={toggleAllFiltered}
                    aria-label="Select all visible vouchers"
                  />
                </th>
                <th className="px-4 py-3">Code</th>
                <th className="px-4 py-3">Package</th>
                <th className="px-4 py-3">Username</th>
                <th className="px-4 py-3">Price</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Router</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filtered.length === 0 ? (
                <tr><td className="table-cell text-slate-500" colSpan="8">No vouchers found.</td></tr>
              ) : filtered.map((voucher) => (
                <tr key={voucher.id}>
                  <td className="table-cell">
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(voucher.id)}
                      onChange={() => toggleVoucherSelection(voucher.id)}
                      aria-label={`Select voucher ${voucher.code}`}
                    />
                  </td>
                  <td className="table-cell font-medium text-slate-950">{voucher.code}</td>
                  <td className="table-cell">{voucher.package}</td>
                  <td className="table-cell">{voucher.username}</td>
                  <td className="table-cell">Ksh {voucher.price}</td>
                  <td className="table-cell"><StatusBadge status={voucher.status || 'active'} /></td>
                  <td className="table-cell"><StatusBadge status={voucher.router_status === 'provisioned' ? 'active' : voucher.router_status} /></td>
                  <td className="table-cell">
                    <div className="flex justify-end gap-2">
                      <button className="btn-secondary" type="button" onClick={() => copyCode(voucher.code)}>
                        <Copy size={14} />
                        Copy
                      </button>
                      <button className="btn-secondary" type="button" onClick={() => expireVoucher(voucher)} disabled={busyId === voucher.id || voucher.status === 'expired'}>
                        <XCircle size={14} />
                        Expire
                      </button>
                      <button className="btn-danger" type="button" onClick={() => deleteVoucher(voucher)} disabled={!voucher.id || busyId === voucher.id}>
                        <Trash2 size={14} />
                        Delete
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
        <Modal title="Create Voucher" onClose={() => setModalOpen(false)}>
          <form className="space-y-4" onSubmit={createVoucher}>
            <div className="flex items-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
              <WalletCards size={18} className="mt-0.5 text-app-accent" />
              <div>
                <p className="text-sm font-semibold text-slate-950">Generate voucher code</p>
                <p className="text-xs text-slate-500">Choose a Hotspot package and the system will create the code and router user.</p>
              </div>
            </div>
            <div>
              <label className="form-label" htmlFor="package_id">Hotspot package</label>
              <select id="package_id" className="form-input" value={packageId} onChange={(event) => setPackageId(event.target.value)}>
                <option value="">Select Hotspot package</option>
                {packages.map((pkg) => <option key={pkg.id} value={pkg.id}>{pkg.name} - Ksh {pkg.price}</option>)}
              </select>
            </div>
            <div className="flex justify-end gap-3 border-t border-slate-200 pt-4">
              <button type="button" className="btn-secondary" onClick={() => setModalOpen(false)}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={creating}>
                <Plus size={15} />
                {creating ? 'Generating...' : 'Generate voucher'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
