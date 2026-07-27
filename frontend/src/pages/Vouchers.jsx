import { Copy, Plus, Search, WalletCards } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import StatusBadge from '../components/StatusBadge';

export default function Vouchers() {
  const [vouchers, setVouchers] = useState([]);
  const [packages, setPackages] = useState([]);
  const [packageId, setPackageId] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    Promise.all([api.get('/vouchers'), api.get('/packages')]).then(([vouchersResponse, packagesResponse]) => {
      const voucherData = vouchersResponse.data;
      const packageData = packagesResponse.data;
      setVouchers(Array.isArray(voucherData) ? voucherData : voucherData?.results || []);
      setPackages((Array.isArray(packageData) ? packageData : packageData?.results || []).filter((item) => (item.service_type || 'hotspot').toLowerCase() === 'hotspot'));
    }).catch((error) => toast.error(error.response?.data?.message || 'Failed to load vouchers'));
  }, []);

  const filtered = useMemo(() => vouchers.filter((item) => `${item.code} ${item.package}`.toLowerCase().includes(query.toLowerCase())), [query, vouchers]);

  const createVoucher = async (event) => {
    event.preventDefault();
    if (!packageId) return toast.error('Select a Hotspot package');
    try {
      const { data } = await api.post('/vouchers', { package_id: packageId });
      setVouchers((current) => [data.voucher, ...current]);
      setPackageId('');
      toast.success(`Voucher ${data.voucher.code} created`);
    } catch (error) { toast.error(error.response?.data?.message || 'Failed to create voucher'); }
  };

  const copyCode = (code) => { navigator.clipboard?.writeText(code); toast.success('Voucher code copied'); };

  return <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
    <form className="surface-card p-4" onSubmit={createVoucher}>
      <div className="flex items-center gap-2 border-b border-slate-200 pb-3"><WalletCards size={18} className="text-app-navy" /><div><h1 className="page-title">Vouchers</h1><p className="page-subtitle">Generate Hotspot vouchers and provision them on MikroTik.</p></div></div>
      <div className="mt-4"><select className="form-input" value={packageId} onChange={(event) => setPackageId(event.target.value)}><option value="">Select Hotspot package</option>{packages.map((pkg) => <option key={pkg.id} value={pkg.id}>{pkg.name} — Ksh {pkg.price}</option>)}</select></div>
      <button type="submit" className="btn-primary mt-4 w-full"><Plus size={15} />Generate voucher</button>
    </form>
    <section className="surface-card"><div className="flex items-center justify-between gap-3 border-b border-slate-200 p-4"><label className="relative block w-full max-w-sm"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} /><input className="form-input mt-0 pl-9" placeholder="Search vouchers" value={query} onChange={(event) => setQuery(event.target.value)} /></label><span className="text-xs text-slate-500">{filtered.length} vouchers</span></div>
      <div className="overflow-x-auto"><table className="min-w-[760px] divide-y divide-slate-200"><thead className="table-head"><tr><th className="px-4 py-3">Code</th><th className="px-4 py-3">Package</th><th className="px-4 py-3">Username</th><th className="px-4 py-3">Price</th><th className="px-4 py-3">Router</th><th className="px-4 py-3 text-right">Actions</th></tr></thead><tbody className="divide-y divide-slate-100">{filtered.map((voucher) => <tr key={voucher.id}><td className="table-cell font-medium text-slate-950">{voucher.code}</td><td className="table-cell">{voucher.package}</td><td className="table-cell">{voucher.username}</td><td className="table-cell">Ksh {voucher.price}</td><td className="table-cell"><StatusBadge status={voucher.router_status === 'provisioned' ? 'active' : voucher.router_status} /></td><td className="table-cell text-right"><button className="btn-secondary" type="button" onClick={() => copyCode(voucher.code)}><Copy size={14} />Copy code</button></td></tr>)}</tbody></table></div>
    </section>
  </div>;
}
