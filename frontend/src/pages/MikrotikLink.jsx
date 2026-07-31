import {
  CheckCircle2,
  Clipboard,
  Network,
  PlugZap,
  RefreshCw,
  Router,
  Wifi,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function portLabel(name) {
  return String(name || '')
    .replace(/^ether\s*(\d+)$/i, 'Ether $1')
    .replace(/^ether(\d+)$/i, 'Ether $1')
    .replace(/^wlan\s*(\d+)$/i, 'Wlan $1')
    .replace(/^wlan(\d+)$/i, 'Wlan $1')
    .replace(/^wifi\s*(\d+)$/i, 'Wifi $1')
    .replace(/^wifi(\d+)$/i, 'Wifi $1');
}

function serviceLabel(service) {
  if (service === 'pppoe') return 'PPPoE';
  if (service === 'hotspot') return 'Hotspot';
  if (service === 'both') return 'PPPoE + Hotspot';
  return String(service || '').toUpperCase();
}

export default function MikrotikSettings() {
  const [provisioningState, setProvisioningState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [provision, setProvision] = useState(null);
  const [routerStatus, setRouterStatus] = useState(null);
  const [pullingStatus, setPullingStatus] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [selectedServices, setSelectedServices] = useState(['pppoe']);
  const [selectedPorts, setSelectedPorts] = useState([]);

  const configured = useMemo(
    () => Boolean(
      provisioningState?.lastSeenAt
      || provisioningState?.status === 'completed'
      || provisioningState?.status === 'script_downloaded',
    ),
    [provisioningState],
  );

  const isReprovisioning = useMemo(() => new URLSearchParams(window.location.search).get('reprovision') === '1', []);
  const isAddingRouter = useMemo(() => new URLSearchParams(window.location.search).get('add') === '1', []);
  const showProvisioning = !configured || isReprovisioning || isAddingRouter;

  const selectedService = useMemo(() => {
    if (selectedServices.includes('pppoe') && selectedServices.includes('hotspot')) return 'both';
    return selectedServices[0] || '';
  }, [selectedServices]);

  const pullRouterStatus = async (options = {}) => {
    setPullingStatus(true);
    try {
      const { data } = await api.get('/router/status');
      setRouterStatus(data);
      if (!options.silent) {
        const fromSnapshot = data.source === 'provisioning_snapshot' || data.source === 'provisioning_seen';
        toast.success(fromSnapshot ? 'Loaded router provisioning state' : 'Router status pulled');
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to pull router status');
    } finally {
      setPullingStatus(false);
    }
  };

  async function loadConfig() {
    setLoading(true);
    try {
      const { data } = await api.get('/settings/mikrotik');
      if (isAddingRouter) {
        // Adding a router is an isolated provisioning session. Do not expose
        // the previously linked router's snapshot or status in this screen.
        setProvisioningState({ status: '', provisionedAt: '', lastSeenAt: '', lastSeenIp: '', identity: '', version: '', board: '' });
        setRouterStatus(null);
        return;
      }
      setProvisioningState({
        status: data.mikrotik_provisioning_status || '',
        provisionedAt: data.mikrotik_provisioned_at || '',
        lastSeenAt: data.mikrotik_last_seen_at || '',
        lastSeenIp: data.mikrotik_last_seen_ip || '',
        identity: data.mikrotik_detected_identity || '',
        version: data.mikrotik_detected_version || '',
        board: data.mikrotik_detected_board || '',
      });
      if (!isAddingRouter && (data.mikrotik_last_seen_at || data.mikrotik_provisioning_status === 'completed')) {
        pullRouterStatus({ silent: true });
      }
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load MikroTik settings');
    } finally {
      setLoading(false);
    }
  }

  async function loadProvisionCommand() {
    try {
      const { data } = await api.get(`/router/provision-command${isAddingRouter ? '?fresh=1' : ''}`);
      setProvision(data);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to create provisioning command');
    }
  }

  useEffect(() => {
    loadConfig();
    loadProvisionCommand();
  }, []);

  const copy = async (value, label) => {
    try {
      await navigator.clipboard.writeText(value || '');
      toast.success(`${label} copied`);
    } catch {
      toast.error('Copy failed');
    }
  };

  const togglePort = (portName) => {
    setSelectedPorts((current) => (
      current.includes(portName)
        ? current.filter((name) => name !== portName)
        : [...current, portName]
    ));
  };

  const toggleService = (service) => {
    setSelectedServices((current) => {
      if (current.includes(service)) {
        return current.length === 1 ? current : current.filter((item) => item !== service);
      }
      const order = ['pppoe', 'hotspot'];
      return [...current, service].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    });
  };

  const assignPorts = async () => {
    if (!selectedService) {
      toast.error('Select PPPoE, Hotspot, or both');
      return;
    }
    if (selectedPorts.length === 0) {
      toast.error('Select at least one router port');
      return;
    }
    setAssigning(true);
    try {
      const profile = selectedService === 'hotspot' ? 'billing-saas-captive' : 'default';
      await Promise.all(selectedPorts.map((portName) => api.post('/router/ports', {
        interface: portName,
        service_type: selectedService,
        profile,
      })));
      toast.success(`${selectedPorts.length} port${selectedPorts.length === 1 ? '' : 's'} queued for ${serviceLabel(selectedService)}`);
      setSelectedPorts([]);
      await pullRouterStatus();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to assign port');
    } finally {
      setAssigning(false);
    }
  };

  if (loading) return <p className="text-sm font-medium text-slate-600">Loading MikroTik configuration...</p>;

  return (
    <div className="space-y-4">
      {showProvisioning && (
        <section id="link-mikrotik" className="surface-card p-4">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="page-title">{isAddingRouter ? 'Add another MikroTik device' : 'Link MikroTik Device'}</h1>
              
            </div>
            <div className={`inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium ${configured ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>
              {configured ? <CheckCircle2 size={17} /> : <Router size={17} />}
              {configured ? 'Configured' : 'Needs setup'}
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-sm font-bold text-slate-950">Provisioning command</h2>
              <p className="mt-1 text-sm text-slate-600">Run this command in the MikroTik terminal. The router links itself to this system and reports its configuration without requiring API host, username, or password.</p>
            </div>
            <button type="button" className="btn-secondary" onClick={loadProvisionCommand}>
              <RefreshCw size={16} />
              New Command
            </button>
          </div>
          <div className="mt-4 rounded-lg bg-slate-950 p-4 text-white">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-bold uppercase tracking-wide text-slate-300">Router terminal</span>
              <button type="button" className="btn-secondary border-slate-700 bg-slate-800 text-white hover:bg-slate-700" onClick={() => copy(provision?.command, 'Command')} disabled={!provision?.command}>
                <Clipboard size={15} />
                Copy
              </button>
            </div>
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-800 p-3 text-xs leading-6 text-slate-100">{provision?.command || 'Generating command...'}</pre>
          </div>
          {provision?.preflight_command && (
            <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-bold uppercase tracking-wide text-slate-500">WAN/DNS preflight</span>
                <button type="button" className="btn-secondary" onClick={() => copy(provision.preflight_command, 'Preflight command')}>
                  <Clipboard size={15} />
                  Copy
                </button>
              </div>
              <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded-md bg-white p-3 text-xs leading-6 text-slate-700">{provision.preflight_command}</pre>
            </div>
          )}
          {provision && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase text-slate-500">Expires</p>
                <p className="mt-1 font-mono text-sm text-slate-900">{provision.expires_at}</p>
              </div>
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase text-slate-500">Purpose</p>
                <p className="mt-1 text-sm font-semibold text-slate-900">Link router, report ports, and prepare captive portal</p>
              </div>
            </div>
          )}
          {provisioningState?.status === 'completed' && (
            <div className="mt-3 grid gap-3 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <p className="text-xs font-semibold uppercase">Router callback</p>
                <p className="mt-1 font-semibold">Received</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase">Identity</p>
                <p className="mt-1 font-semibold">{provisioningState.identity || '-'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase">RouterOS</p>
                <p className="mt-1 font-semibold">{provisioningState.version || '-'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase">Seen from</p>
                <p className="mt-1 font-semibold">{provisioningState.lastSeenIp || '-'}</p>
              </div>
            </div>
          )}
        </section>
      )}

      <section className="rounded-[10px] border-1 border-slate-300 bg-white p-4 sm:p-8">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-950">Router configurations</h2>
            <p className="mt-1 text-sm text-slate-600">Select PPPoE, Hotspot, or both, choose the physical ports, then assign.</p>
          </div>
          <button type="button" className="btn-secondary" onClick={pullRouterStatus} disabled={pullingStatus}>
            <Network size={17} />
            {pullingStatus ? 'Pulling...' : 'Pull Router Config'}
          </button>
        </div>

        {routerStatus ? (
          <div className="space-y-5">
            {(routerStatus.source === 'provisioning_snapshot' || routerStatus.source === 'provisioning_seen') && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                {routerStatus.message || 'Showing the last config pushed by the router during provisioning.'}
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ['Board', routerStatus.device?.board_name || provisioningState?.board || '-'],
                ['RouterOS', routerStatus.device?.version || provisioningState?.version || '-'],
                ['CPU', `${routerStatus.device?.cpu_load ?? '-'}%`],
                ['Uptime', routerStatus.device?.uptime || '-'],
              ].map(([label, value]) => (
                <div key={label} className="flex min-h-16 items-center border border-slate-300 justify-center rounded-2xl shadow-2xl bg-white px-4 text-center">
                  <div>
                    <p className="text-xs font-semibold uppercase text-slate-500">{label}</p>
                    <p className="mt-1 text-sm font-semibold text-slate-950">{value}</p>
                  </div>
                </div>
              ))}
            </div>

            <div className="rounded-[10px] border border-slate-300 shadow-2xs bodder-white bg-white p-4 sm:p-6">
              <div className="grid gap-4 sm:grid-cols-2">
                {[
                  ['pppoe', PlugZap, 'PPPoE'],
                  ['hotspot', Wifi, 'Hotspot'],
                ].map(([key, Icon, label]) => {
                  const active = selectedServices.includes(key);
                  return (
                    <label
                      key={key}
                      className={`flex h-12 cursor-pointer items-center rounded-full border px-4 text-sm font-semibold transition ${active ? 'border-blue-600 bg-blue-50 text-slate-950' : 'border-slate-400 bg-white text-slate-700 hover:border-blue-300'}`}
                    >
                      <input
                        className="sr-only"
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleService(key)}
                      />
                      <span className={`mr-4 flex h-7 w-7 items-center justify-center rounded-full border-2 ${active ? 'border-blue-600 bg-blue-600 text-white' : 'border-blue-600 bg-white text-blue-600'}`}>
                        <Icon size={15} />
                      </span>
                      <span className="flex-1 text-center">{label}</span>
                    </label>
                  );
                })}
              </div>

              <div className="mt-6 grid gap-4 sm:grid-cols-2">
                {(routerStatus.interfaces || []).map((item) => {
                  const selected = selectedPorts.includes(item.name);
                  const assigned = routerStatus.assignments?.[item.name];
                  return (
                    <button
                      key={item.id || item.name}
                      type="button"
                      className={`h-13 rounded-[20px] border px-4 py-4 text-sm font-semibold transition ${selected ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-400 bg-white text-slate-700 hover:border-blue-300'} ${assigned ? 'ring-2 ring-orange-200' : ''}`}
                      onClick={() => togglePort(item.name)}
                      title={assigned ? `${portLabel(item.name)} assigned to ${serviceLabel(assigned.service_type)}` : portLabel(item.name)}
                    >
                      {portLabel(item.name)}
                    </button>
                  );
                })}
              </div>

              {(routerStatus.interfaces || []).length === 0 && (
                <div className="mt-6 rounded-2xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
                  No assignable physical ports have been reported yet.
                </div>
              )}

              <div className="mt-6 flex justify-end">
                <button type="button" className="h-11 min-w-56 rounded-full border border-slate-400 bg-white px-6 text-sm font-semibold text-slate-800 transition hover:border-blue-400 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={assignPorts} disabled={assigning || selectedPorts.length === 0}>
                  {assigning ? 'Assigning...' : `Assign ${serviceLabel(selectedService)}`}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-3xl border border-slate-400 bg-white p-8 text-center">
            <p className="text-sm font-semibold text-slate-700">Pull the router configuration to view board details and assign ports.</p>
          </div>
        )}
      </section>
    </div>
  );
}
