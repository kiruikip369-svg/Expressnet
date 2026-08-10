import { Edit, HelpCircle, Link as LinkIcon, MoreHorizontal, RefreshCw, Search, Trash2, Wifi, WifiOff, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';

function toDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

function isFreshLastSeen(value) {
  const date = toDate(value);
  return date ? Date.now() - date.getTime() <= 3 * 60 * 1000 : false;
}

function formatLastSeen(value) {
  const date = toDate(value);
  if (!date) return 'Never';
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return date.toLocaleString();
}

function formatMemory(value) {
  const bytes = Number(value || 0);
  if (!bytes) return '-';
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB free`;
  return `${Math.round(bytes / (1024 * 1024))} MB free`;
}

export default function MikrotikSettings() {
  const navigate = useNavigate();
  const [config, setConfig] = useState(null);
  const [routerStatus, setRouterStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [workingId, setWorkingId] = useState('');
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [openActionId, setOpenActionId] = useState('');
  const [actionPosition, setActionPosition] = useState(null);

  const rows = useMemo(() => {
    const linked = config?.linked_routers || {};
    return Object.entries(linked).map(([id, item]) => ({
      id,
      boardName: routerStatus?.device?.board_name || item.board_name || item.identity || 'MikroTik Router',
      provisioning: item.provisioning_status || config?.mikrotik_provisioning_status || 'pending',
      cpu: routerStatus?.device?.cpu_load ?? item.cpu_load,
      memory: routerStatus?.device?.free_memory ?? item.free_memory,
      status: routerStatus?.connection_status || item.status || (isFreshLastSeen(item.last_seen_at || config?.mikrotik_last_seen_at) ? 'online' : 'offline'),
      remoteWinbox: routerStatus?.last_seen_ip || item.last_seen_ip || config?.mikrotik_host || '-',
      lastSeenAt: routerStatus?.last_seen_at || item.last_seen_at || config?.mikrotik_last_seen_at,
      source: routerStatus?.source || 'stored',
      ports: routerStatus?.interfaces?.length ?? item.interface_count,
      version: routerStatus?.device?.version || item.version,
    }));
  }, [config, routerStatus]);

  const counts = useMemo(() => ({
    all: rows.length,
    online: rows.filter((item) => item.status === 'online').length,
    offline: rows.filter((item) => item.status !== 'online').length,
  }), [rows]);

  const filteredRows = rows.filter((item) => {
    const text = `${item.boardName} ${item.remoteWinbox} ${item.provisioning}`.toLowerCase();
    if (!text.includes(search.toLowerCase())) return false;
    if (filter === 'online') return item.status === 'online';
    if (filter === 'offline') return item.status !== 'online';
    return true;
  });

  async function loadConfig({ silent = false } = {}) {
    if (!silent) setLoading(true);
    try {
      const { data } = await api.get('/settings/mikrotik');
      setConfig(data);
      if (Object.keys(data.linked_routers || {}).length > 0 || data.mikrotik_last_seen_at) {
        try {
          const status = await api.get('/router/status');
          setRouterStatus(status.data);
        } catch {
          setRouterStatus(null);
        }
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load MikroTik routers');
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => {
    loadConfig();
    const interval = window.setInterval(() => {
      loadConfig({ silent: true });
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  const editRouter = async (routerId) => {
    setOpenActionId('');
    setWorkingId(`edit:${routerId}`);
    try {
      const { data } = await api.get('/router/status');
      setRouterStatus(data);
      toast.success('Router configuration pulled');
      navigate('/mikrotik/link?edit=1');
    } catch (error) {
      setRouterStatus(null);
      toast.error(error.response?.data?.message || 'Failed to pull router configuration');
    } finally {
      setWorkingId('');
    }
  };

  const suspendRouter = async (routerId) => {
    setOpenActionId('');
    setWorkingId(`suspend:${routerId}`);
    try {
      const { data } = await api.post('/router/suspend');
      toast.success(data.message || 'Router suspension queued');
      await loadConfig();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to suspend router');
    } finally {
      setWorkingId('');
    }
  };

  const deleteRouter = async (routerId) => {
    if (!window.confirm('Delete this linked MikroTik router from the account?')) return;
    setOpenActionId('');
    setWorkingId(`delete:${routerId}`);
    try {
      const { data } = await api.delete('/router/delete');
      toast.success(data.message || 'Router deleted');
      setRouterStatus(null);
      await loadConfig();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete router');
    } finally {
      setWorkingId('');
    }
  };

  const reprovisionRouter = () => {
    setOpenActionId('');
    navigate('/mikrotik/link?reprovision=1');
  };

  if (loading) return <p className="text-sm font-medium text-slate-600">Loading MikroTik routers...</p>;

  return (
    <div className="space-y-6">
      <section>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="page-title">MikroTik Routers</h1>
            <p className="page-subtitle">Routers are linked by running the provisioning command. No API host, username, or password is required.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={() => toast('Tutorial content will open here when added.')}>
              <HelpCircle size={16} />
              Tutorial
            </button>
            <button type="button" className="btn-primary" onClick={() => navigate('/mikrotik/link?add=1')}>
              <LinkIcon size={16} />
              Link a MikroTik
            </button>
          </div>
        </div>
      </section>

      <section>
        <div className="border-b border-slate-200">
          <div className="flex flex-wrap gap-5">
            {[
              ['all', 'All', counts.all, WifiOff],
              ['online', 'Online', counts.online, Wifi],
              ['offline', 'Offline', counts.offline, WifiOff],
            ].map(([key, label, count, Icon]) => (
              <button
                key={key}
                type="button"
                className={`inline-flex h-10 items-center gap-2 border-b px-1 text-xs font-medium ${
                  filter === key ? 'border-[var(--app-accent)] text-[var(--app-accent)]' : 'border-transparent text-slate-600'
                }`}
                onClick={() => setFilter(key)}
              >
                <Icon size={15} className="text-slate-400" />
                {label}
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${key === 'online' ? 'bg-green-50 text-green-700' : key === 'offline' ? 'bg-red-50 text-red-600' : ''}`} style={key === 'all' ? { background: 'var(--app-accent-muted)', color: 'var(--app-accent)' } : undefined}>{count}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 table-shell">
          <div className="flex justify-end border-b border-slate-200 p-3">
            <label className="relative block w-full sm:w-72">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} />
              <input className="form-input mt-0 pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search" />
            </label>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-[980px] divide-y divide-slate-200">
              <thead className="table-head">
                <tr>
                  <th className="px-4 py-3">Board Name</th>
                  <th className="px-4 py-3">Provisioning</th>
                  <th className="px-4 py-3">CPU</th>
                  <th className="px-4 py-3">Memory</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Last Seen</th>
                  <th className="px-4 py-3">Source</th>
                  <th className="px-4 py-3">Router IP</th>
                  <th className="px-4 py-3">Ports</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredRows.length === 0 ? (
                  <tr>
                    <td className="table-cell text-slate-500" colSpan="10">No MikroTik routers linked yet.</td>
                  </tr>
                ) : filteredRows.map((router) => (
                  <tr key={router.id}>
                    <td className="table-cell font-medium text-slate-950">{router.boardName}</td>
                    <td className="table-cell"><span className="rounded px-2 py-1 text-[11px] font-medium" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>{router.provisioning}</span></td>
                    <td className="table-cell">{router.cpu === undefined ? '-' : <span className="rounded bg-green-50 px-2 py-1 text-[11px] font-medium text-green-700">{router.cpu}%</span>}</td>
                    <td className="table-cell">{router.memory ? <span className="rounded px-2 py-1 text-[11px] font-medium" style={{ background: 'var(--app-accent-muted)', color: 'var(--app-accent)' }}>{formatMemory(router.memory)}</span> : '-'}</td>
                    <td className="table-cell">
                      <span className={`rounded px-2 py-1 text-[11px] font-medium ${router.status === 'online' ? 'bg-green-50 text-green-700' : router.status === 'suspended' ? 'bg-amber-50 text-amber-700' : 'bg-red-50 text-red-600'}`}>
                        {router.status === 'online' ? 'Online' : router.status === 'suspended' ? 'Suspended' : 'Offline'}
                      </span>
                    </td>
                    <td className="table-cell">{formatLastSeen(router.lastSeenAt)}</td>
                    <td className="table-cell"><span className="rounded bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-700">{router.source === 'routeros_api' ? 'Live API' : router.source === 'agent_report' ? 'Agent' : 'Stored'}</span></td>
                    <td className="table-cell text-green-700">{router.remoteWinbox}</td>
                    <td className="table-cell">{router.ports ?? '-'}</td>
                    <td className="table-cell">
                      <div className="relative flex justify-end">
                        <button
                          type="button"
                          className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:border-[var(--app-accent-soft)] hover:text-[var(--app-accent)] disabled:cursor-not-allowed disabled:opacity-50"
                          onClick={(event) => {
                            if (openActionId === router.id) {
                              setOpenActionId('');
                              setActionPosition(null);
                              return;
                            }
                            const rect = event.currentTarget.getBoundingClientRect();
                            setOpenActionId(router.id);
                            setActionPosition({ top: rect.bottom + 4, left: rect.right - 176 });
                          }}
                          disabled={Boolean(workingId)}
                          aria-label={`Open actions for ${router.boardName}`}
                          aria-expanded={openActionId === router.id}
                        >
                          <MoreHorizontal size={18} />
                        </button>

                        {openActionId === router.id && (
                          <div style={{ top: actionPosition?.top, left: actionPosition?.left }} className="fixed z-[9999] w-44 rounded-md border border-slate-200 bg-white py-1 text-left shadow-lg">
                            <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-[var(--app-accent-muted)] hover:text-[var(--app-accent)]" onClick={() => editRouter(router.id)}>
                              <Edit size={14} />
                              Edit
                            </button>
                            <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50" onClick={() => suspendRouter(router.id)}>
                              <XCircle size={14} />
                              Suspend
                            </button>
                            <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50" onClick={reprovisionRouter}>
                              <RefreshCw size={14} />
                              Reprovision
                            </button>
                            <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-red-600 hover:bg-red-50" onClick={() => deleteRouter(router.id)}>
                              <Trash2 size={14} />
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 text-xs text-slate-600">
            <span>Showing {filteredRows.length} result{filteredRows.length === 1 ? '' : 's'}</span>
            <span className="rounded-md border border-slate-200 px-3 py-2">Per page&nbsp;&nbsp;10</span>
          </div>
        </div>
      </section>
    </div>
  );
}
