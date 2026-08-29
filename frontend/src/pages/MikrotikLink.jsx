import {
  ArrowRight,
  CheckCircle2,
  Clipboard,
  EthernetPort,
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
  const [activeStep, setActiveStep] = useState('link');
  const [selectedServices, setSelectedServices] = useState(['hotspot']);
  const [selectedPorts, setSelectedPorts] = useState([]);
  const [routerName, setRouterName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [migrationMode, setMigrationMode] = useState(false);

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
      return data;
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to pull router status');
      return null;
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
        setRouterName('');
        setActiveStep('link');
        return;
      }
      const nextProvisioningState = {
        status: data.mikrotik_provisioning_status || '',
        provisionedAt: data.mikrotik_provisioned_at || '',
        lastSeenAt: data.mikrotik_last_seen_at || '',
        lastSeenIp: data.mikrotik_last_seen_ip || '',
        identity: data.mikrotik_detected_identity || '',
        version: data.mikrotik_detected_version || '',
        board: data.mikrotik_detected_board || '',
      };
      setRouterName(data.mikrotik_name || data.router_name || data.mikrotik_detected_identity || '');
      setProvisioningState({
        ...nextProvisioningState,
      });
      const nextConfigured = Boolean(
        nextProvisioningState.lastSeenAt
        || nextProvisioningState.status === 'completed'
        || nextProvisioningState.status === 'script_downloaded',
      );
      setActiveStep((current) => {
        if (current === 'configure') return current;
        return nextConfigured && !isReprovisioning ? 'configure' : 'link';
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
      const params = new URLSearchParams();
      if (isAddingRouter) params.set('fresh', '1');
      if (migrationMode) params.set('migrate', '1');
      const query = params.toString();
      const { data } = await api.get(`/router/provision-command${query ? `?${query}` : ''}`);
      setProvision(data);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to create provisioning command');
    }
  }

  useEffect(() => {
    loadConfig();
    loadProvisionCommand();
  }, []);

  useEffect(() => {
    loadProvisionCommand();
  }, [migrationMode]);

  const copy = async (value, label) => {
    try {
      await navigator.clipboard.writeText(value || '');
      toast.success(`${label} copied`);
    } catch {
      toast.error('Copy failed');
    }
  };

  const goToConfiguration = async () => {
    const data = await pullRouterStatus();
    if (data) setActiveStep('configure');
  };

  const saveRouterName = async () => {
    setSavingName(true);
    try {
      await api.patch('/settings/mikrotik', { mikrotik_name: routerName.trim() });
      toast.success('MikroTik name saved');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save MikroTik name');
    } finally {
      setSavingName(false);
    }
  };

  const togglePort = (portName) => {
    const port = (routerStatus?.interfaces || []).find((item) => item.name === portName);
    if (port && port.customer_assignable === false) {
      toast.error(`${portLabel(portName)} is the WAN/uplink port. Choose a customer LAN port.`);
      return;
    }
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
        <section id="link-mikrotik" className={`surface-card p-4 ${activeStep === 'link' ? '' : 'hidden'}`}>
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
              <p className="mt-1 text-sm text-slate-600">Run this command in the MikroTik terminal. Use the Copy button so the URL stays plain RouterOS text.</p>
            </div>
            <button type="button" className="btn-secondary" onClick={loadProvisionCommand}>
              <RefreshCw size={16} />
              New Command
            </button>
          </div>
          <div className="mt-4 grid gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <label className="block">
              <span className="form-label">MikroTik name</span>
              <input
                className="form-input"
                value={routerName}
                onChange={(event) => setRouterName(event.target.value)}
                placeholder="Main office router"
              />
            </label>
            <button type="button" className="btn-secondary h-11" onClick={saveRouterName} disabled={savingName}>
              {savingName ? 'Saving...' : 'Save Name'}
            </button>
          </div>
          <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-lg border border-slate-200 bg-white p-4">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-slate-300 text-[var(--app-accent)] focus:ring-[var(--app-accent)]"
              checked={migrationMode}
              onChange={(event) => setMigrationMode(event.target.checked)}
            />
            <span>
              <span className="block text-sm font-semibold text-slate-950">Migrate from another system</span>
              <span className="mt-1 block text-sm text-slate-600">
                Export existing PPPoE secrets, Hotspot customers, package profiles, and active sessions first. Run normal provisioning after the export has completed.
              </span>
            </span>
          </label>
          <div className="mt-4 overflow-hidden rounded-lg border border-slate-800 bg-slate-950 text-white shadow-sm">
            <div className="grid gap-3 bg-slate-800 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0">
                <p className="text-sm font-semibold leading-5 text-slate-50">Run the command below in the MikroTik terminal if the device mode is not set to advanced mode</p>
                <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-900/70 px-3 py-2 text-xs leading-5 text-slate-100">/system/device-mode/update mode=advanced</pre>
              </div>
              <button type="button" className="btn-secondary justify-center border-slate-700 bg-slate-900 text-white hover:bg-slate-700 sm:w-auto" onClick={() => copy('/system/device-mode/update mode=advanced', 'Advanced mode command')}>
                <Clipboard size={15} />
                Copy
              </button>
            </div>
            <div className="grid gap-3 px-4 pt-4 sm:grid-cols-[1fr_auto] sm:items-center">
              <span className="text-xs font-bold uppercase tracking-wide text-slate-300">{provision?.mode === 'migration' ? 'Migration export command' : 'Router terminal'}</span>
              <button type="button" className="btn-secondary justify-center border-slate-700 bg-slate-800 text-white hover:bg-slate-700 sm:w-auto" onClick={() => copy(provision?.command, 'Command')} disabled={!provision?.command}>
                <Clipboard size={15} />
                Copy
              </button>
            </div>
            <textarea
              className="mx-4 mb-4 mt-3 block h-36 w-[calc(100%-2rem)] resize-none rounded-md border border-slate-700 bg-slate-800 p-4 font-mono text-xs leading-6 text-slate-100 outline-none"
              readOnly
              spellCheck="false"
              value={provision?.command || 'Generating command...'}
              onFocus={(event) => event.target.select()}
            />
            {provision?.mode === 'migration' && (
              <p className="px-4 pb-4 text-xs font-semibold text-amber-200">This command only saves the current router data into Expressnet. Turn migration off afterward to generate the normal provisioning command.</p>
            )}
          </div>
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
          <div className="mt-4 flex justify-end">
            <button type="button" className="btn-primary" onClick={goToConfiguration} disabled={pullingStatus}>
              {pullingStatus ? 'Pulling live config...' : 'Next'}
              <ArrowRight size={16} />
            </button>
          </div>
        </section>
      )}

      {(!showProvisioning || activeStep === 'configure') && (
      <section className="rounded-[10px] border-1 border-slate-300 bg-white p-4 sm:p-8">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-950">Router configurations</h2>
            <p className="mt-1 text-sm text-slate-600">Select one service for the customer ports. Choosing PPPoE + Hotspot applies both services to every selected port.</p>
          </div>
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
                ['Name', routerName || routerStatus.device?.identity || provisioningState?.identity || '-'],
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
                      className="flex h-12 cursor-pointer items-center rounded-full border border-slate-400 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-[var(--app-accent)]"
                      style={active ? { borderColor: 'var(--app-accent)', background: 'var(--app-accent-muted)', color: 'var(--app-text)' } : undefined}
                    >
                      <input
                        className="sr-only"
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleService(key)}
                      />
                      <span
                        className="mr-4 flex h-7 w-7 items-center justify-center rounded-full border-2"
                        style={active ? { borderColor: 'var(--app-accent)', background: 'var(--app-accent)', color: 'var(--app-accent-contrast)' } : { borderColor: 'var(--app-accent)', background: 'var(--app-panel)', color: 'var(--app-accent)' }}
                      >
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
                  const blocked = item.customer_assignable === false;
                  return (
                    <button
                      key={item.id || item.name}
                      type="button"
                      className={`min-h-16 rounded-[20px] border border-slate-400 bg-white px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-[var(--app-accent)] disabled:cursor-not-allowed disabled:opacity-50 ${assigned ? 'ring-2 ring-[var(--app-focus-ring)]' : ''}`}
                      style={selected ? { borderColor: 'var(--app-accent)', background: 'var(--app-accent)', color: 'var(--app-accent-contrast)' } : assigned ? { borderColor: 'var(--app-accent)', background: 'var(--app-accent-muted)' } : undefined}
                      onClick={() => togglePort(item.name)}
                      disabled={blocked}
                      title={blocked ? `${portLabel(item.name)} is ${item.assignment_warning || 'not assignable'}` : assigned ? `${portLabel(item.name)} assigned to ${serviceLabel(assigned.service_type)}` : portLabel(item.name)}
                      aria-pressed={selected}
                    >
                      <span className="flex items-center justify-center gap-2">
                        <EthernetPort size={18} />
                        {portLabel(item.name)}{blocked ? ' - WAN' : ''}
                      </span>
                      {selected && (
                        <span className="mt-1 block text-[11px] font-bold uppercase">
                          Selected
                        </span>
                      )}
                      {assigned && (
                        <span className="mt-1 block text-[11px] font-bold uppercase text-[var(--app-accent)]">
                          {serviceLabel(assigned.service_type)} assigned
                        </span>
                      )}
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
                <button type="button" className="h-11 min-w-56 rounded-full border border-slate-400 bg-white px-6 text-sm font-semibold text-slate-800 transition hover:border-[var(--app-accent)] hover:text-[var(--app-accent)] disabled:cursor-not-allowed disabled:opacity-50" onClick={assignPorts} disabled={assigning || selectedPorts.length === 0}>
                  {assigning ? 'Assigning...' : `Assign ${serviceLabel(selectedService)} to selected ports`}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-3xl border border-slate-400 bg-white p-8 text-center">
            <p className="text-sm font-semibold text-slate-700">Live router configuration was not available yet. Go back and click Next after the router has reported in.</p>
          </div>
        )}
      </section>
      )}
    </div>
  );
}
