import { Activity, AlertTriangle, Cpu, Filter, Gauge, Radio, TrendingUp, Users, Wifi } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';

function readDarkMode() {
  try {
    const settings = JSON.parse(localStorage.getItem('tenant_settings') || '{}');
    const mode = settings.themeMode || (settings.darkMode ? 'dark' : 'light');
    if (mode === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches;
    return mode === 'dark';
  } catch {
    return false;
  }
}

function formatKES(value) {
  return `KSh ${Number(value || 0).toLocaleString('en-KE')}`;
}

function formatData(value) {
  if (typeof value === 'string') return value;
  const gb = Number(value || 0) / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(gb >= 10 ? 0 : 1)} GB`;
  const mb = Number(value || 0) / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

function formatBitrate(value) {
  const bps = Number(value || 0);
  if (bps >= 1_000_000_000) return `${(bps / 1_000_000_000).toFixed(1)} Gbps`;
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`;
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(0)} Kbps`;
  return `${bps.toFixed(0)} bps`;
}

function decodeToken(token) {
  try {
    const payload = token.split('.')[1];
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=');
    return JSON.parse(window.atob(padded));
  } catch {
    return null;
  }
}

function isTenantAdmin(tenant, token) {
  const decoded = token ? decodeToken(token) : null;
  const role = String(tenant?.role || tenant?.user_role || decoded?.role || '').toLowerCase();
  return (
    role === 'admin' ||
    role === 'tenant_admin' ||
    role === 'owner' ||
    tenant?.is_admin === true ||
    tenant?.is_owner === true ||
    decoded?.is_admin === true ||
    decoded?.is_owner === true ||
    (!role && Boolean(tenant?.id))
  );
}

function StatCard({ icon: Icon, label, value, helper, children }) {
  return (
    <section className="rounded-lg bg-[#ffb17a] px-4 py-4 text-[#261001] shadow-[0_18px_30px_rgba(15,23,42,0.12)]">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase">{label}</p>
        <Icon size={18} />
      </div>
      <p className="mt-3 text-2xl font-bold">{value}</p>
      {helper ? <p className="mt-1 text-xs font-semibold opacity-75">{helper}</p> : null}
      {children}
    </section>
  );
}

function OverviewCard({ icon: Icon, label, value, helper, percent, darkMode }) {
  const bounded = Math.max(0, Math.min(Number(percent ?? 0), 100));
  return (
    <section className={`rounded-lg border p-4 ${darkMode ? 'border-[#33343a] bg-[#222326] text-white' : 'border-slate-200 bg-white text-slate-950'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-[#ffefe2] text-[#d96f00]"><Icon size={18} /></span>
          <div>
            <h2 className="text-sm font-semibold">{label}</h2>
            <p className={`text-[11px] ${darkMode ? 'text-[#a9aec3]' : 'text-slate-500'}`}>{helper}</p>
          </div>
        </div>
        <p className="text-lg font-bold">{value}</p>
      </div>
      <div className={`mt-4 h-2 overflow-hidden rounded-full ${darkMode ? 'bg-[#34353b]' : 'bg-slate-100'}`}>
        <div className="h-full rounded-full bg-[#fa8200]" style={{ width: `${bounded}%` }} />
      </div>
    </section>
  );
}

export default function Dashboard() {
  const { tenant, token } = useAuth();
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [darkMode, setDarkMode] = useState(() => readDarkMode());

  useEffect(() => {
    setDarkMode(readDarkMode());
    let mounted = true;
    async function load() {
      try {
        const { data } = await api.get('/dashboard/stats');
        if (mounted) setDashboard(data);
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load dashboard');
      } finally {
        if (mounted) setLoading(false);
      }
    }
    load();
    return () => { mounted = false; };
  }, []);

  const summary = dashboard?.summary || {};
  const router = dashboard?.router_health || {};
  const topHotspotUsers = useMemo(() => {
    const users = dashboard?.top_hotspot_active_users || dashboard?.most_active_users || [];
    return users.slice(0, 5);
  }, [dashboard]);

  const stats = {
    totalUsers: Number(summary.total_customers || 0),
    pppoeUsers: Number(summary.pppoe_customers || 0),
    hotspotUsers: Number(summary.hotspot_customers || 0),
    activeUsers: Number(summary.active_customers || 0),
    dailyEarnings: Number(summary.revenue_today || 0),
  };

  const trafficBps = Number(router.network_traffic_bps || 0);
  const trafficValue = Number(router.traffic_percent ?? router.network_traffic_percent ?? 0);
  const cpuValue = Number(router.cpu_load_percent ?? router.cpu_load ?? 0);
  const internetStrength = Number(router.internet_strength_percent ?? (router.status === 'online' ? 96 : router.status === 'offline' ? 0 : 62));
  const trafficLabel = trafficBps ? formatBitrate(trafficBps) : `${trafficValue}%`;
  const sourceLabel = router.sample_source === 'routeros_api' ? 'Live MikroTik sample' : 'Latest router snapshot';
  const canViewEarnings = isTenantAdmin(tenant, token);

  if (loading) {
    return <div className={`rounded-lg p-4 text-xs ${darkMode ? 'bg-[#17181b] text-[#c8ccdc]' : 'bg-white text-slate-600'}`}>Loading dashboard...</div>;
  }

  return (
    <div className={`min-h-[calc(100vh-96px)] space-y-5 rounded-lg p-4 ${darkMode ? 'bg-[#17181b] text-white' : 'bg-white text-slate-950'}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="inline-flex h-8 items-center gap-2 rounded-md bg-red-600 px-3 text-[11px] font-semibold text-white"><AlertTriangle size={14} />Expires in 2 days. Click to renew</button>
          <button type="button" className={`inline-flex h-8 items-center gap-2 rounded-md border px-3 text-[11px] font-semibold ${darkMode ? 'border-[#3a3b40] bg-[#25262a] text-white' : 'border-slate-200 bg-slate-50 text-slate-700'}`}><Filter size={14} />Filters</button>
        </div>
      </div>

      <section className={`grid gap-4 ${canViewEarnings ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}`}>
        <StatCard icon={Users} label="Total users" value={stats.totalUsers.toLocaleString('en-KE')}>
          <div className="mt-4 grid grid-cols-2 gap-2 text-xs font-semibold">
            <span className="rounded-md bg-white/35 px-3 py-2">PPPoE: {stats.pppoeUsers.toLocaleString('en-KE')}</span>
            <span className="rounded-md bg-white/35 px-3 py-2">Hotspot: {stats.hotspotUsers.toLocaleString('en-KE')}</span>
          </div>
        </StatCard>
        <StatCard icon={Activity} label="Active users" value={stats.activeUsers.toLocaleString('en-KE')} helper="Customers currently marked active" />
        {canViewEarnings && (
          <StatCard icon={TrendingUp} label="Daily earnings" value={formatKES(stats.dailyEarnings)} helper="Successful payments today" />
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Dashboard Overview</h2>
        <div className="grid gap-4 xl:grid-cols-2">
          <OverviewCard darkMode={darkMode} icon={Wifi} label="Internet strength" value={`${internetStrength}%`} helper={router.status ? `${sourceLabel} - ${router.status}` : sourceLabel} percent={internetStrength} />
          <OverviewCard darkMode={darkMode} icon={Radio} label="Network traffic" value={trafficLabel} helper="Current live router throughput" percent={trafficValue} />
          <OverviewCard darkMode={darkMode} icon={Cpu} label="CPU status" value={`${cpuValue}%`} helper={router.board_name || sourceLabel} percent={cpuValue} />
          <section className={`rounded-lg border ${darkMode ? 'border-[#33343a] bg-[#222326] text-white' : 'border-slate-200 bg-white text-slate-950'}`}>
            <div className={`flex items-center gap-2 border-b px-4 py-3 ${darkMode ? 'border-[#36373d]' : 'border-slate-200'}`}>
              <Gauge size={17} className="text-[#fa8200]" />
              <div>
                <h2 className="text-sm font-semibold">Top 5 hotspot active users</h2>
                <p className={`text-[11px] ${darkMode ? 'text-[#a9aec3]' : 'text-slate-500'}`}>Ranked by data usage</p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead className={`${darkMode ? 'bg-[#2a2b2f] text-[#d5d7e2]' : 'bg-slate-50 text-slate-600'} text-left uppercase`}>
                  <tr><th className="px-4 py-3">User</th><th className="px-4 py-3">Data used</th><th className="px-4 py-3">Phone</th></tr>
                </thead>
                <tbody className={darkMode ? 'divide-y divide-[#34353b]' : 'divide-y divide-slate-100'}>
                  {topHotspotUsers.length ? topHotspotUsers.map((user) => (
                    <tr key={user.username || user.phone}>
                      <td className="px-4 py-3 font-semibold text-[#d96f00]">{user.username || '-'}</td>
                      <td className="px-4 py-3">{formatData(user.data_used)}</td>
                      <td className="px-4 py-3">{user.phone || '-'}</td>
                    </tr>
                  )) : (
                    <tr><td className="px-4 py-8 text-center text-slate-500" colSpan="3">No active hotspot users found.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </section>
    </div>
  );
}
