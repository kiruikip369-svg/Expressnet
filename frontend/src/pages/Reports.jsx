import { CheckCheck, ChevronDown, Download, Eye, MoreVertical, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function toDate(value) {
  if (!value) return null;
  if (value._seconds) return new Date(value._seconds * 1000);
  if (value.seconds) return new Date(value.seconds * 1000);
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

function formatKES(value) {
  return `Ksh ${Number(value || 0).toLocaleString('en-KE', { minimumFractionDigits: 2 })}`;
}

function formatDate(value) {
  const date = toDate(value);
  if (!date) return '-';
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

const ACCENT = 'var(--app-accent)';
const ACCENT_SOFT = 'var(--app-accent-soft)';
const GRID = '#e5e7eb';

const fallbackSeries = {
  payments: [['Jan', 23000], ['Feb', 18000], ['Mar', 21000], ['Apr', 14000], ['May', 14000], ['Jun', 200]],
  activeUsers: [['Mon', 36, 0], ['Tue', 19, 0], ['Wed', 5, 0], ['Thu', 2, 0], ['Fri', 1, 0]],
  retention: [['Jan', 130, 70, 38], ['Feb', 138, 68, 37], ['Mar', 72, 68, 39], ['Apr', 66, 67, 34], ['May', 50, 63, 43], ['Jun', 10, 28, 75]],
  dataUsage: [['27 May', 62], ['28 May', 38], ['29 May', 49], ['30 May', 50], ['31 May', 75], ['01 Jun', 45]],
  sms: [['Thu', 70], ['Fri', 190], ['Sat', 190], ['Sun', 170], ['Mon', 190], ['Tue', 40]],
};

const reportCopy = {
  finance: {
    title: 'Financial Report',
    subtitle: 'Payments, revenue, settlements, and money movement.',
    exportName: 'financial-report.csv',
  },
  management: {
    title: 'Management Report',
    subtitle: 'Work progress, staff activity, operational tasks, and customer follow-up.',
    exportName: 'management-report.csv',
  },
  network: {
    title: 'Network Report',
    subtitle: 'Router health, sessions, traffic, provisioning, and MikroTik activity.',
    exportName: 'network-report.csv',
  },
};

function maxValue(data, index = 1) {
  return Math.max(...data.map((item) => Number(item[index]) || 0), 1);
}

function points(data, index, width = 320, height = 190, pad = 18) {
  const max = maxValue(data, index);
  return data.map((item, i) => {
    const x = pad + (i * (width - pad * 2)) / Math.max(data.length - 1, 1);
    const y = height - pad - ((Number(item[index]) || 0) / max) * (height - pad * 2);
    return `${x},${y}`;
  }).join(' ');
}

function ChartCard({ title, subtitle, children, action = 'This week' }) {
  return (
    <section className="theme-card min-h-[318px] rounded-lg border">
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="theme-text text-sm font-medium">{title}</h2>
          <p className="theme-muted mt-1 text-[11px]">{subtitle}</p>
        </div>
        <button type="button" className="theme-card-muted h-8 rounded-md border px-3 text-[11px] font-medium">{action}</button>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function BarChart({ data, valueIndex = 1, height = 190 }) {
  const max = maxValue(data, valueIndex);
  return (
    <div className="flex items-end gap-3" style={{ height }}>
      {data.map((item) => (
        <div key={item[0]} className="flex flex-1 flex-col items-center gap-2">
          <div className="w-full max-w-[26px] rounded-t-sm" style={{ height: `${Math.max(((Number(item[valueIndex]) || 0) / max) * (height - 28), item[valueIndex] ? 5 : 1)}px`, background: ACCENT }} />
          <span className="text-[10px] text-slate-500">{item[0]}</span>
        </div>
      ))}
    </div>
  );
}

function LineChart({ data, indexes = [1], colors = [ACCENT], height = 190 }) {
  return (
    <svg viewBox="0 0 320 190" className="h-full w-full" style={{ minHeight: height }}>
      {[0, 1, 2, 3].map((line) => <line key={line} x1="18" x2="304" y1={24 + line * 42} y2={24 + line * 42} stroke={GRID} strokeWidth="1" />)}
      {indexes.map((index, lineIndex) => <polyline key={index} fill="none" stroke={colors[lineIndex]} strokeWidth="3" points={points(data, index)} />)}
      {indexes.map((index, lineIndex) => points(data, index).split(' ').map((point) => {
        const [cx, cy] = point.split(',');
        return <circle key={`${index}-${point}`} cx={cx} cy={cy} r="3.5" fill={colors[lineIndex]} stroke="#fff" strokeWidth="1" />;
      }))}
      {data.map((item, index) => <text key={item[0]} x={18 + (index * 286) / Math.max(data.length - 1, 1)} y="184" textAnchor="middle" fill="#64748b" fontSize="9">{String(item[0]).split(' ')[0]}</text>)}
    </svg>
  );
}

function sameDay(a, b) {
  return a && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function exportCsv(rows, filename = 'report.csv') {
  const headers = ['customer_name', 'phone', 'payment_code', 'amount', 'status', 'paid_at', 'provider'];
  const csv = [headers.join(','), ...rows.map((item) => headers.map((key) => JSON.stringify(item[key] ?? '')).join(','))].join('\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function MetricCard({ title, value, helper }) {
  return (
    <div className="rounded-md p-4 shadow-[0_18px_30px_rgba(15,23,42,0.12)]" style={{ background: 'var(--app-accent-soft)', color: 'var(--app-text)' }}>
      <p className="text-xs font-medium">{title}</p>
      <div className="mt-3 flex items-center gap-2">
        <p className="text-xl font-semibold">{formatKES(value)}</p>
        <Eye size={14} />
      </div>
      <p className="mt-2 text-xs">{helper}</p>
    </div>
  );
}

function PlainMetricCard({ title, value, helper }) {
  return (
    <div className="theme-card rounded-md border p-4">
      <p className="theme-muted text-xs font-medium">{title}</p>
      <p className="theme-text mt-3 text-2xl font-semibold">{value}</p>
      <p className="theme-muted mt-2 text-xs">{helper}</p>
    </div>
  );
}

export default function Reports({ type = 'finance' }) {
  const reportType = reportCopy[type] ? type : 'finance';
  const copy = reportCopy[reportType];
  const [payments, setPayments] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState('checked');

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [paymentsResponse, dashboardResponse] = await Promise.all([
          api.get('/payments?page_size=100'),
          api.get('/dashboard/stats'),
        ]);
        const paymentData = paymentsResponse.data;
        setPayments(Array.isArray(paymentData) ? paymentData : paymentData.results || []);
        setDashboard(dashboardResponse.data);
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load report');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const successfulPayments = useMemo(() => payments.filter((payment) => payment.status === 'success'), [payments]);
  const rows = useMemo(() => {
    const base = tab === 'checked' ? successfulPayments : payments.filter((payment) => payment.status !== 'success');
    const needle = query.toLowerCase();
    return base.filter((payment) => `${payment.customer_name || ''} ${payment.phone || ''} ${payment.payment_code || ''}`.toLowerCase().includes(needle));
  }, [payments, query, successfulPayments, tab]);

  const totals = useMemo(() => {
    const now = new Date();
    const weekStart = new Date(now);
    weekStart.setDate(now.getDate() - now.getDay());
    weekStart.setHours(0, 0, 0, 0);
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const sumSince = (start) => successfulPayments.reduce((sum, payment) => {
      const date = toDate(payment.paid_at || payment.created_at);
      return date && date >= start ? sum + Number(payment.amount || 0) : sum;
    }, 0);
    return {
      daily: successfulPayments.reduce((sum, payment) => (sameDay(toDate(payment.paid_at || payment.created_at), now) ? sum + Number(payment.amount || 0) : sum), 0),
      weekly: sumSince(weekStart),
      monthly: sumSince(monthStart),
    };
  }, [successfulPayments]);

  const chartData = useMemo(() => ({
    payments: dashboard?.payments_chart?.length ? dashboard.payments_chart : fallbackSeries.payments,
    activeUsers: dashboard?.active_users_chart?.length ? dashboard.active_users_chart : fallbackSeries.activeUsers,
    retention: dashboard?.retention_chart?.length ? dashboard.retention_chart : fallbackSeries.retention,
    dataUsage: dashboard?.data_usage_chart?.length ? dashboard.data_usage_chart : fallbackSeries.dataUsage,
    forecast: dashboard?.revenue_forecast?.length ? dashboard.revenue_forecast : fallbackSeries.payments,
    sms: dashboard?.sms_chart?.length ? dashboard.sms_chart : fallbackSeries.sms,
  }), [dashboard]);

  const routerCount = Number(dashboard?.router_count || dashboard?.active_routers || 0);
  const totalUsers = Number(dashboard?.total_customers || dashboard?.total_users || 0);
  const activeUsers = Number(dashboard?.active_customers || dashboard?.active_users || 0);
  const staffCount = Number(dashboard?.staff_count || dashboard?.team_members || 0);
  const taskCount = Number(dashboard?.open_tickets || dashboard?.tasks_open || 0);

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div>
            <h1 className="theme-text text-xl font-medium">{copy.title}</h1>
            <p className="theme-muted mt-1 text-xs">{copy.subtitle}</p>
          </div>
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-900 text-[10px]">i</span>
        </div>
        <button type="button" className="btn-primary h-9 px-4 shadow-md" onClick={() => exportCsv(rows, copy.exportName)}>
          <Download size={14} />
          Export Report
        </button>
      </div>

      {reportType === 'finance' && (
        <section className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard title="Daily Earnings" value={totals.daily} helper="Total earnings today" />
          <MetricCard title="Weekly Earnings" value={totals.weekly} helper="Total earnings this week" />
          <MetricCard title="Monthly Earnings" value={totals.monthly} helper="Total earnings this month" />
          <MetricCard title="Mobile Money (This Month)" value={totals.monthly} helper="Excluding voucher payments" />
        </section>
      )}

      {reportType === 'management' && (
        <section className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          <PlainMetricCard title="Open Tasks" value={taskCount.toLocaleString('en-KE')} helper="Current staff work queue" />
          <PlainMetricCard title="Staff Members" value={staffCount.toLocaleString('en-KE')} helper="Team members with system access" />
          <PlainMetricCard title="Active Customers" value={activeUsers.toLocaleString('en-KE')} helper="Customers requiring ongoing service" />
          <PlainMetricCard title="Total Customers" value={totalUsers.toLocaleString('en-KE')} helper="Operational customer base" />
        </section>
      )}

      {reportType === 'network' && (
        <section className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          <PlainMetricCard title="Routers" value={routerCount.toLocaleString('en-KE')} helper="Linked MikroTik routers" />
          <PlainMetricCard title="Active Users" value={activeUsers.toLocaleString('en-KE')} helper="Current service activity" />
          <PlainMetricCard title="PPPoE Users" value={Number(dashboard?.pppoe_customers || 0).toLocaleString('en-KE')} helper="Router-backed PPPoE customers" />
          <PlainMetricCard title="Hotspot Users" value={Number(dashboard?.hotspot_customers || 0).toLocaleString('en-KE')} helper="Captive portal users" />
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-2">
        {reportType === 'finance' && (
          <>
            <ChartCard title="Payments" subtitle="Payments and expenses trend."><BarChart data={chartData.payments} height={230} /></ChartCard>
            <ChartCard title="Revenue Forecast" subtitle="Expected revenue trend."><LineChart data={chartData.forecast} indexes={[1]} colors={[ACCENT]} height={230} /></ChartCard>
          </>
        )}
        {reportType === 'management' && (
          <>
            <ChartCard title="Work Progress" subtitle="Open, active, and completed work movement."><LineChart data={chartData.activeUsers} indexes={[1, 2]} colors={[ACCENT, ACCENT_SOFT]} height={230} /></ChartCard>
            <ChartCard title="Staff Communication" subtitle="SMS and customer follow-up activity."><BarChart data={chartData.sms} height={230} /></ChartCard>
            <ChartCard title="Customer Retention" subtitle="Recurring and active customer movement."><LineChart data={chartData.retention} indexes={[1, 2, 3]} colors={[ACCENT, '#16a34a', '#ef4444']} height={230} /></ChartCard>
          </>
        )}
        {reportType === 'network' && (
          <>
            <ChartCard title="Data Usage" subtitle="Router data usage trend for PPPoE and Hotspot users."><LineChart data={chartData.dataUsage} indexes={[1]} colors={[ACCENT]} height={230} /></ChartCard>
            <ChartCard title="Active Sessions" subtitle="Router-backed active and new sessions."><LineChart data={chartData.activeUsers} indexes={[1, 2]} colors={[ACCENT, ACCENT_SOFT]} height={230} /></ChartCard>
          </>
        )}
      </section>

      {reportType === 'finance' && <section className="border-b border-slate-200">
        <div className="flex gap-6">
          {[
            ['checked', 'Checked payments', CheckCheck],
            ['unchecked', 'Unchecked payments', X],
          ].map(([key, label, Icon]) => (
            <button key={key} type="button" className={`inline-flex h-10 items-center gap-2 border-b-2 text-xs font-medium ${tab === key ? 'border-[var(--app-accent)] text-[var(--app-accent)]' : 'border-transparent text-slate-500'}`} onClick={() => setTab(key)}>
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </section>}

      {reportType === 'finance' && <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex justify-end border-b border-slate-200 p-3">
          <label className="relative block w-full max-w-xs">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input className="form-input mt-0 h-9 pl-9" placeholder="Search" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[980px] w-full">
            <thead className="bg-slate-50 text-left text-xs font-semibold text-black">
              <tr>
                <th className="w-12 px-5 py-4"><input type="checkbox" className="h-4 w-4 rounded border-slate-300" /></th>
                {['User', 'Phone', 'Receipt No.', 'Amount', 'Checked', 'Paid At', 'Disbursement'].map((heading) => (
                  <th key={heading} className="px-5 py-4">
                    <span className="inline-flex items-center gap-1">{heading}<ChevronDown size={15} className="text-slate-400" /></span>
                  </th>
                ))}
                <th className="px-5 py-4" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs text-black">
              {loading ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="9">Loading report...</td></tr>
              ) : rows.length === 0 ? (
                <tr><td className="px-5 py-10 text-center text-slate-500" colSpan="9">No payments found.</td></tr>
              ) : rows.map((payment) => (
                <tr key={payment.id}>
                  <td className="px-5 py-4"><input type="checkbox" className="h-4 w-4 rounded border-slate-300" /></td>
                  <td className="px-5 py-4 font-bold" style={{ color: 'var(--app-accent)' }}>{payment.customer_name || payment.access_username || '-'}</td>
                  <td className="px-5 py-4">{payment.phone || '-'}</td>
                  <td className="px-5 py-4">{payment.payment_code || '-'}</td>
                  <td className="px-5 py-4">{formatKES(payment.amount)}</td>
                  <td className="px-5 py-4"><span className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] text-emerald-700">{payment.status === 'success' ? 'Yes' : 'No'}</span></td>
                  <td className="px-5 py-4">{formatDate(payment.paid_at || payment.created_at)}</td>
                  <td className="px-5 py-4"><span className="rounded-md border px-2 py-1 text-[10px]" style={{ borderColor: 'var(--app-accent-soft)', background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>{payment.provider === 'voucher' ? 'Voucher' : 'Direct'}</span></td>
                  <td className="px-5 py-4 text-right" style={{ color: 'var(--app-accent)' }}><MoreVertical size={16} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>}
    </div>
  );
}
