import {
  Activity,
  AlertTriangle,
  Box,
  Cpu,
  FileText,
  Filter,
  Gauge,
  MessageSquare,
  PackagePlus,
  Radio,
  Receipt,
  RefreshCw,
  Router,
  UserPlus,
  Users,
  Wallet,
  Wifi,
  Zap,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Link } from 'react-router-dom';
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
  const bytes = Number(value || 0);
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(bytes >= 10737418240 ? 0 : 1)} GB`;
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes.toFixed(0)} B`;
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
  return ['admin', 'tenant_admin', 'owner'].includes(role) || tenant?.is_admin === true || tenant?.is_owner === true || decoded?.is_admin === true || decoded?.is_owner === true || (!role && Boolean(tenant?.id));
}

function Card({ children, className = '' }) {
  return <section className={`theme-card rounded-lg border shadow-sm ${className}`}>{children}</section>;
}

function Sparkline() {
  return (
    <svg viewBox="0 0 240 52" className="mt-3 h-12 w-full" aria-hidden="true">
      <path d="M0 41 L18 35 L36 37 L54 28 L72 31 L90 26 L108 34 L126 36 L144 25 L162 40 L180 27 L198 39 L216 25 L240 39" fill="none" stroke="var(--app-accent)" strokeWidth="2" />
      <path d="M0 41 L18 35 L36 37 L54 28 L72 31 L90 26 L108 34 L126 36 L144 25 L162 40 L180 27 L198 39 L216 25 L240 39 L240 52 L0 52 Z" fill="var(--app-accent-muted)" opacity="0.72" />
    </svg>
  );
}

function KpiCard({ icon: Icon, title, value, helper, pills = [], action, to, children }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}><Icon size={20} /></span>
          <div>
            <p className="theme-muted text-xs font-medium">{title}</p>
            <p className="theme-text mt-1 text-2xl font-medium leading-none">{value}</p>
            {helper && <p className="mt-2 text-xs text-slate-500">{helper}</p>}
          </div>
        </div>
        <span className="text-xl leading-none text-slate-400">...</span>
      </div>
      {children}
      {pills.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {pills.map((pill) => <span key={pill} className="rounded-md px-2.5 py-1 text-[11px] font-medium" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-text)' }}>{pill}</span>)}
        </div>
      )}
      {action && (
        <Link to={to} className="mt-4 inline-flex items-center gap-3 border-t border-slate-100 pt-3 text-xs font-medium" style={{ color: 'var(--app-accent)' }}>
          {action}
          <span aria-hidden="true">{'->'}</span>
        </Link>
      )}
    </Card>
  );
}

function MetricTile({ icon: Icon, title, value, helper, percent }) {
  const width = Math.max(0, Math.min(Number(percent || 0), 100));
  return (
    <div className="theme-card-muted rounded-md border p-3">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-md" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}><Icon size={16} /></span>
        <div className="min-w-0">
          <p className="theme-muted text-[11px] font-medium">{title}</p>
          <p className="theme-text mt-1 text-xl font-medium leading-none">{value}</p>
          <p className="mt-2 truncate text-xs text-slate-500">{helper || '-'}</p>
        </div>
      </div>
      {percent !== undefined && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-200">
          <div className="h-full rounded-full" style={{ width: `${width}%`, background: 'var(--app-accent)' }} />
        </div>
      )}
    </div>
  );
}

function TrafficChart({ rx, tx }) {
  const max = Math.max(Number(rx || 0), Number(tx || 0), 1);
  const upload = [0.1, 0.15, 0.12, 0.18, 0.16, 0.2, 0.17].map((v, i) => `${i * 40},${104 - (v * tx / max * 70)}`);
  const download = [0.2, 0.18, 0.22, 0.19, 0.24, 0.21, 0.25].map((v, i) => `${i * 40},${104 - (v * rx / max * 70)}`);
  return (
    <div className="theme-card-muted rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="theme-text text-xs font-medium">Traffic (Last 24h)</h3>
        <div className="flex gap-4 text-[10px] font-medium text-slate-500">
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-4 rounded-full" style={{ background: 'var(--app-accent)' }} />Upload</span>
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-4 rounded-full bg-blue-500" />Download</span>
        </div>
      </div>
      <svg viewBox="0 0 280 130" className="mt-4 h-36 w-full" aria-label="Router traffic chart">
        {[0, 1, 2, 3, 4].map((i) => <line key={`h-${i}`} x1="0" x2="280" y1={20 + i * 22} y2={20 + i * 22} stroke="#e2e8f0" />)}
        {[0, 1, 2, 3, 4].map((i) => <line key={`v-${i}`} y1="16" y2="108" x1={i * 70} x2={i * 70} stroke="#eef2f7" />)}
        <polyline points={upload.join(' ')} fill="none" stroke="var(--app-accent)" strokeWidth="2" />
        <polyline points={download.join(' ')} fill="none" stroke="#3b82f6" strokeWidth="2" />
        <text x="0" y="122" fontSize="10" fill="#64748b">00:00</text>
        <text x="74" y="122" fontSize="10" fill="#64748b">06:00</text>
        <text x="142" y="122" fontSize="10" fill="#64748b">12:00</text>
        <text x="210" y="122" fontSize="10" fill="#64748b">18:00</text>
        <text x="252" y="122" fontSize="10" fill="#64748b">24:00</text>
      </svg>
    </div>
  );
}

function Donut({ rows }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0) || 1;
  let offset = 25;
  const colors = ['var(--app-accent)', '#3b82f6', '#f59e0b', '#94a3b8'];
  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 42 42" className="h-28 w-28 shrink-0">
        <circle cx="21" cy="21" r="15.915" fill="transparent" stroke="#e2e8f0" strokeWidth="8" />
        {rows.map((row, index) => {
          const dash = (row.value / total) * 100;
          const circle = <circle key={row.label} cx="21" cy="21" r="15.915" fill="transparent" stroke={colors[index]} strokeWidth="8" strokeDasharray={`${dash} ${100 - dash}`} strokeDashoffset={offset} />;
          offset -= dash;
          return circle;
        })}
      </svg>
      <div className="flex-1 space-y-3 text-xs">
        {rows.map((row, index) => (
          <div key={row.label} className="flex items-center justify-between gap-3">
            <span className="inline-flex items-center gap-2 text-slate-600"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[index] }} />{row.label}</span>
            <strong className="font-medium text-slate-800">{row.value} ({Math.round((row.value / total) * 100)}%)</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { tenant, token } = useAuth();
  const [dashboard, setDashboard] = useState(null);
  const [routerResources, setRouterResources] = useState(null);
  const [payments, setPayments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [darkMode, setDarkMode] = useState(() => readDarkMode());

  useEffect(() => {
    setDarkMode(readDarkMode());
    let mounted = true;
    async function load({ silent = false } = {}) {
      try {
        if (silent) setRefreshing(true);
        const [statsResult, resourcesResult, paymentsResult] = await Promise.all([
          api.get('/dashboard/stats'),
          api.get('/router/resources').catch((error) => ({ error })),
          api.get('/payments?all=1').catch(() => ({ data: [] })),
        ]);
        if (!mounted) return;
        setDashboard(statsResult.data);
        if (resourcesResult.data) setRouterResources(resourcesResult.data);
        const rows = Array.isArray(paymentsResult.data) ? paymentsResult.data : paymentsResult.data?.results || [];
        setPayments(rows.slice(0, 5));
      } catch (error) {
        if (!silent) toast.error(error.response?.data?.message || 'Failed to load dashboard');
      } finally {
        if (mounted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }
    load();
    const timer = window.setInterval(() => load({ silent: true }), 10000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  const summary = dashboard?.summary || {};
  const statsRouter = dashboard?.router_health || {};
  const router = routerResources
    ? {
        ...statsRouter,
        status: routerResources.live ? 'online' : statsRouter.status,
        board_name: routerResources.board_name || statsRouter.board_name,
        cpu_load_percent: routerResources.cpu_load_percent ?? statsRouter.cpu_load_percent,
        internet_strength_percent: routerResources.internet_strength_percent ?? statsRouter.internet_strength_percent,
        network_traffic_bps: routerResources.network_traffic_bps ?? statsRouter.network_traffic_bps,
        network_rx_bps: routerResources.network_rx_bps ?? statsRouter.network_rx_bps,
        network_tx_bps: routerResources.network_tx_bps ?? statsRouter.network_tx_bps,
        active_sessions: routerResources.active_sessions || statsRouter.active_sessions,
        top_active_sessions: routerResources.top_active_sessions || statsRouter.top_active_sessions,
        sample_source: routerResources.source || statsRouter.sample_source,
        sampled_at: routerResources.sampled_at || statsRouter.sampled_at,
        uptime: routerResources.uptime,
        message: routerResources.message,
      }
    : statsRouter;

  const canViewEarnings = isTenantAdmin(tenant, token);
  const totalUsers = Number(summary.total_customers || 0);
  const pppoeUsers = Number(summary.pppoe_customers || 0);
  const hotspotUsers = Number(summary.hotspot_customers || 0);
  const activeUsers = Number(summary.active_customers || 0);
  const packages = dashboard?.package_utilization || [];
  const activePackages = packages.reduce((sum, item) => sum + Number(item[1] || 0), 0);
  const staticUsers = Math.max(0, totalUsers - pppoeUsers - hotspotUsers);
  const sessions = useMemo(() => (router?.top_active_sessions || []).slice(0, 5), [router]);
  const sampledAt = router.sampled_at ? new Date(router.sampled_at) : null;
  const sourceLabel = router.sample_source === 'routeros_api' ? 'Live MikroTik resource sample' : 'Latest router snapshot';
  const signal = Number(router.internet_strength_percent ?? 0);
  const cpu = Number(router.cpu_load_percent ?? 0);
  const rx = Number(router.network_rx_bps || 0);
  const tx = Number(router.network_tx_bps || 0);
  const activeSessionCount = Number(router.active_sessions?.total || 0);
  const packageRows = [
    { label: 'PPPoE', value: pppoeUsers },
    { label: 'Hotspot', value: hotspotUsers },
    { label: 'Static', value: staticUsers },
    { label: 'Expired', value: Math.max(0, totalUsers - activeUsers) },
  ];

  if (loading) return <div className="theme-card rounded-lg border p-4 text-xs">Loading dashboard...</div>;

  return (
    <div className={`theme-page min-h-[calc(100vh-96px)] space-y-4 rounded-lg p-3 sm:p-4 ${darkMode ? 'text-white' : 'text-slate-950'}`}>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="theme-muted text-[11px]">Business / <span className="font-medium text-slate-700">{tenant?.business_name || tenant?.name || 'Expressnet'}</span></p>
          <h1 className="theme-text mt-0.5 text-xl font-medium">Dashboard</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex h-8 items-center gap-2 rounded-md px-2 text-[11px] font-medium text-slate-600">
            <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
            Updated: {sampledAt && !Number.isNaN(sampledAt.valueOf()) ? sampledAt.toLocaleTimeString() : '--'}
          </span>
          <button type="button" className="inline-flex h-8 items-center gap-2 rounded-md border border-red-100 bg-red-50 px-3 text-[11px] font-medium text-red-500"><AlertTriangle size={14} />Expires in 2 days. Click to renew</button>
          <button type="button" className="theme-card inline-flex h-8 items-center gap-2 rounded-md border px-3 text-[11px] font-medium text-slate-600"><Filter size={14} />Filters</button>
        </div>
      </div>

      <section className={`grid gap-4 ${canViewEarnings ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
        <KpiCard icon={Users} title="Total Users" value={totalUsers.toLocaleString('en-KE')} pills={[`PPPoE: ${pppoeUsers}`, `Hotspot: ${hotspotUsers}`, `Static: ${staticUsers}`]} action="View users" to="/customers" />
        <KpiCard icon={Activity} title="Active Users" value={activeUsers.toLocaleString('en-KE')} helper="Currently online" action="Live sessions" to="/mikrotik">
          <Sparkline />
        </KpiCard>
        {canViewEarnings && <KpiCard icon={Wallet} title="Daily Earnings" value={formatKES(summary.revenue_today)} helper="Successful payments today" action="View payments" to="/payments" />}
        <KpiCard icon={Box} title="Active Packages" value={activePackages.toLocaleString('en-KE')} helper="Total active packages" action="Manage packages" to="/packages" />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="theme-text text-base font-medium">Router Overview</h2>
              <p className="text-xs text-slate-500">{router.message || sourceLabel}</p>
            </div>
            <span className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium ${router.status === 'online' ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}>
              <span className="h-2 w-2 rounded-full bg-current" />{router.status || 'offline'}
            </span>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-[0.92fr_1.08fr]">
            <div className="grid gap-3 sm:grid-cols-2">
              <MetricTile icon={Wifi} title="Internet Strength" value={`${signal}%`} helper={router.board_name || '--'} percent={signal} />
              <MetricTile icon={Radio} title="Network Traffic" value={formatBitrate(rx + tx)} helper={`RX ${formatBitrate(rx)} / TX ${formatBitrate(tx)}`} />
              <MetricTile icon={Cpu} title="CPU Status" value={`${cpu}%`} helper={router.board_name || '--'} />
              <MetricTile icon={Gauge} title="Uptime" value={router.uptime || '0s'} helper={router.sample_source || '--'} />
            </div>
            <TrafficChart rx={rx} tx={tx} />
          </div>
        </Card>

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-4">
            <h2 className="theme-text text-base font-medium">Top Active Sessions</h2>
            <span className="text-xs text-slate-500">{activeSessionCount.toLocaleString('en-KE')} live sessions</span>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="bg-slate-50 text-left text-[10px] font-medium uppercase text-slate-500">
                <tr><th className="px-4 py-3">User</th><th className="px-4 py-3">Service</th><th className="px-4 py-3">Data Used</th><th className="px-4 py-3">Uptime</th><th className="px-4 py-3">Address</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sessions.length ? sessions.map((session) => (
                  <tr key={`${session.service_type}-${session.username}-${session.address}`}>
                    <td className="px-4 py-3 font-medium text-slate-900">{session.username || '-'}</td>
                    <td className="px-4 py-3 text-slate-600">{session.service_type || '-'}</td>
                    <td className="px-4 py-3 text-slate-600">{formatData(session.data_used)}</td>
                    <td className="px-4 py-3 text-slate-600">{session.uptime || '-'}</td>
                    <td className="px-4 py-3 text-slate-600">{session.address || session.mac_address || '-'}</td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan="5" className="px-4 py-12 text-center text-slate-500">
                      <PackagePlus className="mx-auto mb-3 text-slate-300" size={56} />
                      No active router sessions found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_0.95fr_1.5fr]">
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <h2 className="theme-text text-base font-medium">Revenue This Month</h2>
            <button type="button" className="rounded-md border border-slate-200 px-3 py-1 text-[11px] text-slate-600">This Month</button>
          </div>
          <p className="theme-text mt-2 text-2xl font-medium">{formatKES(summary.revenue_this_month)}</p>
          <p className="text-xs text-slate-500">vs last month</p>
          <svg viewBox="0 0 300 72" className="mt-4 h-20 w-full">
            <line x1="0" y1="50" x2="300" y2="50" stroke="#dbeafe" />
            <polyline points="0,50 50,50 100,50 150,50 200,50 250,50 300,50" fill="none" stroke="var(--app-accent)" strokeWidth="2" />
            {[1, 5, 10, 15, 20, 25, 30].map((day, index) => <text key={day} x={index * 49} y="70" fontSize="10" fill="#64748b">{day}</text>)}
          </svg>
        </Card>

        <Card className="p-4">
          <h2 className="theme-text text-base font-medium">Package Distribution</h2>
          <div className="mt-4">
            <Donut rows={packageRows} />
          </div>
        </Card>

        <Card className="p-4">
          <h2 className="theme-text text-base font-medium">Quick Actions</h2>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ['/customers', UserPlus, 'Add Customer'],
              ['/packages', Box, 'Create Package'],
              ['/vouchers', Zap, 'Generate Voucher'],
              ['/expenses', Receipt, 'Add Expense'],
              ['/messages', MessageSquare, 'Send Message'],
              ['/payments', Wallet, 'View Payments'],
              ['/mikrotik', Router, 'Router Status'],
              ['/reports/finance', FileText, 'Reports'],
            ].map(([to, Icon, label, tone]) => (
              <Link key={label} to={to} className="theme-card-muted flex h-14 flex-col items-center justify-center gap-1.5 rounded-md border text-center text-[11px] font-medium text-slate-700 transition hover:bg-slate-100">
                <Icon size={17} style={{ color: 'var(--app-accent)' }} />
                {label}
              </Link>
            ))}
          </div>
        </Card>
      </section>

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-4">
          <h2 className="theme-text text-base font-medium">Recent Payments</h2>
          <Link to="/payments" className="text-xs font-medium" style={{ color: 'var(--app-accent)' }}>View all</Link>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead className="bg-slate-50 text-left text-[10px] font-medium uppercase text-slate-500">
              <tr><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Customer</th><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Method</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Date</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {payments.length ? payments.map((payment) => (
                <tr key={payment.id || payment.reference}>
                  <td className="px-5 py-4 font-medium text-slate-900">{payment.reference || payment.payment_code || '-'}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.customer_name || '-'}</td>
                  <td className="px-5 py-4 text-slate-600">{formatKES(payment.amount)}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.provider || payment.method || '-'}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.status || '-'}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.created_at ? new Date(payment.created_at).toLocaleDateString() : '-'}</td>
                </tr>
              )) : (
                <tr><td colSpan="6" className="px-5 py-8 text-center text-slate-500"><FileText className="mx-auto mb-2 text-slate-300" size={18} />No recent payments</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
