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

// ---------------------------------------------------------------------------
// Shared type scale (kept in one place so headings/labels stay consistent
// across every card instead of drifting between arbitrary px values).
// ---------------------------------------------------------------------------
const TYPE = {
  pageTitle: 'text-2xl font-semibold tracking-tight',
  eyebrow: 'text-xs font-medium',
  cardTitle: 'text-sm font-semibold tracking-tight',
  kpiValue: 'text-2xl font-semibold leading-none tracking-tight',
  metricValue: 'text-xl font-semibold leading-none tracking-tight',
  label: 'text-xs font-medium',
  helper: 'text-xs',
  tableHead: 'text-[11px] font-semibold uppercase tracking-wide',
  tableCell: 'text-xs',
  pill: 'text-xs font-medium',
};

// Hairline / muted stroke color used across every inline SVG chart so grid
// lines and axis labels track the active theme instead of hardcoded slate.
const GRID_LINE = 'var(--app-border, #e2e8f0)';
const AXIS_TEXT = 'var(--app-muted, #64748b)';

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
    <Card className="p-3.5">
      <div className="flex items-start justify-between">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}><Icon size={18} /></span>
          <div>
            <p className={`theme-muted ${TYPE.eyebrow}`}>{title}</p>
            <p className={`theme-text mt-1 ${TYPE.kpiValue}`}>{value}</p>
            {helper && <p className={`theme-muted mt-2 ${TYPE.helper}`}>{helper}</p>}
          </div>
        </div>
      </div>
      {children}
      {pills.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {pills.map((pill) => (
            <span key={pill} className={`rounded-md px-2.5 py-1 ${TYPE.pill}`} style={{ background: 'var(--app-accent-muted)', color: 'var(--app-text)' }}>
              {pill}
            </span>
          ))}
        </div>
      )}
      {action && (
        <Link to={to} className={`mt-4 inline-flex items-center gap-3 border-t pt-3 ${TYPE.eyebrow}`} style={{ borderColor: GRID_LINE, color: 'var(--app-accent)' }}>
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
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}><Icon size={16} /></span>
        <div className="min-w-0">
          <p className={`theme-muted ${TYPE.eyebrow}`}>{title}</p>
          <p className={`theme-text mt-1 ${TYPE.metricValue}`}>{value}</p>
          <p className={`theme-muted mt-2 truncate ${TYPE.helper}`}>{helper || '-'}</p>
        </div>
      </div>
      {percent !== undefined && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full" style={{ background: 'var(--app-panel-muted, #e2e8f0)' }}>
          <div className="h-full rounded-full" style={{ width: `${width}%`, background: 'var(--app-accent)' }} />
        </div>
      )}
    </div>
  );
}

function TrafficChart({ series }) {
  const rows = series.length ? series : [{ rx: 0, tx: 0, sampledAt: Date.now() }];
  const max = Math.max(...rows.map((item) => Math.max(Number(item.rx || 0), Number(item.tx || 0))), 1);
  const width = 280;
  const step = rows.length <= 1 ? 0 : width / (rows.length - 1);
  const y = (value) => 104 - (Number(value || 0) / max) * 84;
  const upload = rows.map((item, index) => `${index * step},${y(item.tx)}`);
  const download = rows.map((item, index) => `${index * step},${y(item.rx)}`);
  const firstTime = rows[0]?.sampledAt ? new Date(rows[0].sampledAt) : null;
  const lastTime = rows[rows.length - 1]?.sampledAt ? new Date(rows[rows.length - 1].sampledAt) : null;
  const formatTime = (date) => (date && !Number.isNaN(date.valueOf()) ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--');

  return (
    <div className="theme-card-muted rounded-md border p-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className={`theme-text ${TYPE.cardTitle}`}>Realtime Traffic</h3>
        <div className={`theme-muted flex gap-4 ${TYPE.eyebrow}`}>
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-4 rounded-full" style={{ background: 'var(--app-accent)' }} />Upload</span>
          <span className="inline-flex items-center gap-1"><span className="h-1.5 w-4 rounded-full" style={{ background: 'var(--app-accent-soft)' }} />Download</span>
        </div>
      </div>
      <svg viewBox="0 0 280 130" className="mt-4 h-36 w-full" aria-label="Router traffic chart">
        {[0, 1, 2, 3, 4].map((i) => <line key={`h-${i}`} x1="0" x2="280" y1={20 + i * 22} y2={20 + i * 22} stroke={GRID_LINE} />)}
        {[0, 1, 2, 3, 4].map((i) => <line key={`v-${i}`} y1="16" y2="108" x1={i * 70} x2={i * 70} stroke={GRID_LINE} opacity="0.6" />)}
        <polyline points={upload.join(' ')} fill="none" stroke="var(--app-accent)" strokeWidth="2" />
        <polyline points={download.join(' ')} fill="none" stroke="var(--app-accent-soft)" strokeWidth="2" />
        <text x="0" y="122" fontSize="10" fill={AXIS_TEXT}>{formatTime(firstTime)}</text>
        <text x="224" y="122" fontSize="10" fill={AXIS_TEXT}>{formatTime(lastTime)}</text>
      </svg>
    </div>
  );
}

function Donut({ rows }) {
  const total = rows.reduce((sum, row) => sum + row.value, 0) || 1;
  let offset = 25;
  const colors = ['var(--app-accent)', 'var(--app-accent-soft)', 'var(--app-focus-ring)', 'var(--app-muted, #94a3b8)'];
  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 42 42" className="h-28 w-28 shrink-0">
        <circle cx="21" cy="21" r="15.915" fill="transparent" stroke={GRID_LINE} strokeWidth="8" />
        {rows.map((row, index) => {
          const dash = (row.value / total) * 100;
          const circle = <circle key={row.label} cx="21" cy="21" r="15.915" fill="transparent" stroke={colors[index]} strokeWidth="8" strokeDasharray={`${dash} ${100 - dash}`} strokeDashoffset={offset} />;
          offset -= dash;
          return circle;
        })}
      </svg>
      <div className={`flex-1 space-y-3 ${TYPE.tableCell}`}>
        {rows.map((row, index) => (
          <div key={row.label} className="flex items-center justify-between gap-3">
            <span className="theme-muted inline-flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[index] }} />{row.label}</span>
            <strong className="theme-text font-semibold">{row.value} ({Math.round((row.value / total) * 100)}%)</strong>
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
  const [trafficSeries, setTrafficSeries] = useState([]);
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
  const enabledUsers = Number(summary.enabled_customers || 0);
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
  const connectedUsers = Math.max(activeUsers, activeSessionCount);

  useEffect(() => {
    if (!dashboard && !routerResources) return;
    const sampledAtValue = router.sampled_at || new Date().toISOString();
    setTrafficSeries((previous) => {
      const sampleKey = String(sampledAtValue);
      const last = previous[previous.length - 1];
      if (last?.sampledAt === sampleKey && last.rx === rx && last.tx === tx) return previous;
      return [...previous, { rx, tx, sampledAt: sampleKey }].slice(-24);
    });
  }, [dashboard, routerResources, router.sampled_at, rx, tx]);
  const packageRows = [
    { label: 'PPPoE', value: pppoeUsers },
    { label: 'Hotspot', value: hotspotUsers },
    { label: 'Static', value: staticUsers },
    { label: 'Inactive', value: Math.max(0, totalUsers - enabledUsers) },
  ];

  const quickActions = [
    ['/customers', UserPlus, 'Add Customer'],
    ['/packages', Box, 'Create Package'],
    ['/vouchers', Zap, 'Generate Voucher'],
    ['/expenses', Receipt, 'Add Expense'],
    ['/messages', MessageSquare, 'Send Message'],
    ['/payments', Wallet, 'View Payments'],
    ['/mikrotik', Router, 'Router Status'],
    ['/reports/finance', FileText, 'Reports'],
  ];

  if (loading) return <div className={`theme-card rounded-lg border p-4 ${TYPE.tableCell}`}>Loading dashboard...</div>;

  return (
    <div className={`theme-page min-h-[calc(100vh-96px)] space-y-4 rounded-lg p-3 sm:p-4 ${darkMode ? 'text-white' : 'text-slate-950'}`}>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <p className={`theme-muted ${TYPE.helper}`}>Business / <span className="theme-text font-medium">{tenant?.business_name || tenant?.name || 'Expressnet'}</span></p>
          <h1 className={`theme-text mt-0.5 ${TYPE.pageTitle}`}>Dashboard</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`theme-muted inline-flex h-8 items-center gap-2 rounded-md px-2 ${TYPE.eyebrow}`}>
            <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
            Updated: {sampledAt && !Number.isNaN(sampledAt.valueOf()) ? sampledAt.toLocaleTimeString() : '--'}
          </span>
          <button type="button" className={`inline-flex h-8 items-center gap-2 rounded-md border px-3 ${TYPE.eyebrow}`} style={{ borderColor: 'var(--app-accent-soft)', background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>
            <AlertTriangle size={14} />Expires in 2 days. Click to renew
          </button>
          <button type="button" className={`theme-card theme-muted inline-flex h-8 items-center gap-2 rounded-md border px-3 ${TYPE.eyebrow}`}>
            <Filter size={14} />Filters
          </button>
        </div>
      </div>

      <section className={`grid gap-4 ${canViewEarnings ? 'xl:grid-cols-4' : 'xl:grid-cols-3'}`}>
        <KpiCard icon={Users} title="Total Users" value={totalUsers.toLocaleString('en-KE')} pills={[`PPPoE: ${pppoeUsers}`, `Hotspot: ${hotspotUsers}`, `Static: ${staticUsers}`]} action="View users" to="/customers" />
        <KpiCard icon={Activity} title="Connected Users" value={connectedUsers.toLocaleString('en-KE')} helper="Currently online" action="Live sessions" to="/mikrotik">
          <Sparkline />
        </KpiCard>
        {canViewEarnings && <KpiCard icon={Wallet} title="Daily Earnings" value={formatKES(summary.daraja_revenue_today ?? summary.revenue_today)} helper="Successful Daraja payments today" action="View payments" to="/payments" />}
        <KpiCard icon={Box} title="Active Packages" value={activePackages.toLocaleString('en-KE')} helper="Total active packages" action="Manage packages" to="/packages" />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className={`theme-text ${TYPE.cardTitle}`}>Router Overview</h2>
              <p className={`theme-muted ${TYPE.helper}`}>{router.message || sourceLabel}</p>
            </div>
            <span className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 ${TYPE.pill}`} style={router.status === 'online' ? { background: 'var(--app-accent-muted)', color: 'var(--app-accent)' } : { background: 'var(--app-panel-muted)', color: 'var(--app-muted)' }}>
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
            <TrafficChart series={trafficSeries} />
          </div>
        </Card>

        <Card className="overflow-hidden">
          <div className="flex items-center justify-between border-b px-4 py-4" style={{ borderColor: GRID_LINE }}>
            <h2 className={`theme-text ${TYPE.cardTitle}`}>Top Active Sessions</h2>
            <span className={`theme-muted ${TYPE.helper}`}>{activeSessionCount.toLocaleString('en-KE')} live sessions</span>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead className={`theme-card-muted text-left ${TYPE.tableHead}`}>
                <tr><th className="px-4 py-3">User</th><th className="px-4 py-3">Service</th><th className="px-4 py-3">Data Used</th><th className="px-4 py-3">Uptime</th><th className="px-4 py-3">Address</th></tr>
              </thead>
              <tbody className="divide-y" style={{ borderColor: GRID_LINE }}>
                {sessions.length ? sessions.map((session) => (
                  <tr key={`${session.service_type}-${session.username}-${session.address}`} className="divide-y" style={{ borderColor: GRID_LINE }}>
                    <td className={`theme-text px-4 py-3 font-medium ${TYPE.tableCell}`}>{session.username || '-'}</td>
                    <td className={`theme-muted px-4 py-3 ${TYPE.tableCell}`}>{session.service_type || '-'}</td>
                    <td className={`theme-muted px-4 py-3 ${TYPE.tableCell}`}>{formatData(session.data_used)}</td>
                    <td className={`theme-muted px-4 py-3 ${TYPE.tableCell}`}>{session.uptime || '-'}</td>
                    <td className={`theme-muted px-4 py-3 ${TYPE.tableCell}`}>{session.address || session.mac_address || '-'}</td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan="5" className={`theme-muted px-4 py-12 text-center ${TYPE.tableCell}`}>
                      <PackagePlus className="mx-auto mb-3" size={56} style={{ color: 'var(--app-muted)', opacity: 0.4 }} />
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
            <h2 className={`theme-text ${TYPE.cardTitle}`}>Revenue This Month</h2>
            <button type="button" className={`theme-card theme-muted rounded-md border px-3 py-1 ${TYPE.eyebrow}`}>This Month</button>
          </div>
          <p className={`theme-text mt-2 ${TYPE.kpiValue}`}>{formatKES(summary.revenue_this_month)}</p>
          <p className={`theme-muted ${TYPE.helper}`}>vs last month</p>
          <svg viewBox="0 0 300 72" className="mt-4 h-20 w-full">
            <line x1="0" y1="50" x2="300" y2="50" stroke={GRID_LINE} />
            <polyline points="0,50 50,50 100,50 150,50 200,50 250,50 300,50" fill="none" stroke="var(--app-accent)" strokeWidth="2" />
            {[1, 5, 10, 15, 20, 25, 30].map((day, index) => <text key={day} x={index * 49} y="70" fontSize="10" fill={AXIS_TEXT}>{day}</text>)}
          </svg>
        </Card>

        <Card className="p-4">
          <h2 className={`theme-text ${TYPE.cardTitle}`}>Package Distribution</h2>
          <div className="mt-4">
            <Donut rows={packageRows} />
          </div>
        </Card>

        <Card className="p-4">
          <h2 className={`theme-text ${TYPE.cardTitle}`}>Quick Actions</h2>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            {quickActions.map(([to, Icon, label]) => (
              <Link
                key={label}
                to={to}
                className={`theme-card-muted flex h-14 flex-col items-center justify-center gap-1.5 rounded-md border text-center ${TYPE.eyebrow} transition hover:bg-[var(--app-accent-muted)] hover:border-[var(--app-accent-soft)]`}
              >
                <Icon size={17} style={{ color: 'var(--app-accent)' }} />
                {label}
              </Link>
            ))}
          </div>
        </Card>
      </section>

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b px-4 py-4" style={{ borderColor: GRID_LINE }}>
          <h2 className={`theme-text ${TYPE.cardTitle}`}>Recent Payments</h2>
          <Link to="/payments" className={`${TYPE.eyebrow}`} style={{ color: 'var(--app-accent)' }}>View all</Link>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className={`theme-card-muted text-left ${TYPE.tableHead}`}>
              <tr><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Customer</th><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Method</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Date</th></tr>
            </thead>
            <tbody className="divide-y" style={{ borderColor: GRID_LINE }}>
              {payments.length ? payments.map((payment) => (
                <tr key={payment.id || payment.reference} className="divide-y" style={{ borderColor: GRID_LINE }}>
                  <td className={`theme-text px-5 py-4 font-medium ${TYPE.tableCell}`}>{payment.reference || payment.payment_code || '-'}</td>
                  <td className={`theme-muted px-5 py-4 ${TYPE.tableCell}`}>{payment.customer_name || '-'}</td>
                  <td className={`theme-muted px-5 py-4 ${TYPE.tableCell}`}>{formatKES(payment.amount)}</td>
                  <td className={`theme-muted px-5 py-4 ${TYPE.tableCell}`}>{payment.provider || payment.method || '-'}</td>
                  <td className={`theme-muted px-5 py-4 ${TYPE.tableCell}`}>{payment.status || '-'}</td>
                  <td className={`theme-muted px-5 py-4 ${TYPE.tableCell}`}>{payment.created_at ? new Date(payment.created_at).toLocaleDateString() : '-'}</td>
                </tr>
              )) : (
                <tr><td colSpan="6" className={`theme-muted px-5 py-8 text-center ${TYPE.tableCell}`}><FileText className="mx-auto mb-2" size={18} style={{ color: 'var(--app-muted)', opacity: 0.5 }} />No recent payments</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
