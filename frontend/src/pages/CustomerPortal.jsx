import { CreditCard, KeyRound, Monitor, Package, Phone, PlugZap, Wifi, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { useParams } from 'react-router-dom';
import axios from 'axios';

const publicApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
});

function submitRouterLogin(routerIp, username, password) {
  if (!routerIp || !username || !password) return false;
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = `http://${routerIp}/login`;
  form.style.display = 'none';
  [
    ['username', username],
    ['password', password],
    ['dst', 'http://connectivitycheck.gstatic.com/generate_204'],
  ].forEach(([name, value]) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  });
  document.body.appendChild(form);
  form.submit();
  return true;
}

function packageType(pkg) {
  return pkg?.service_type === 'pppoe' ? 'pppoe' : 'hotspot';
}

function pathServiceType() {
  if (window.location.pathname.startsWith('/pppoe/') || window.location.pathname.startsWith('/pppoe-renew/')) return 'pppoe';
  if (window.location.pathname.startsWith('/hotspot/')) return 'hotspot';
  return '';
}

function responseItems(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.results)) return data.results;
  return [];
}

export default function CustomerPortal() {
  const { tenantId } = useParams();
  const [tenant, setTenant] = useState(null);
  const [packages, setPackages] = useState([]);
  const [phone, setPhone] = useState('');
  const [serviceType, setServiceType] = useState(pathServiceType() || 'hotspot');
  const [pppoeUsername, setPppoeUsername] = useState('');
  const [macAddress, setMacAddress] = useState('');
  const [selectedPackage, setSelectedPackage] = useState(null);
  const [receiptCode, setReceiptCode] = useState('');
  const [voucherCode, setVoucherCode] = useState('');
  const [accessUsername, setAccessUsername] = useState('');
  const [accessPassword, setAccessPassword] = useState('');
  const [voucherAccess, setVoucherAccess] = useState(null);
  const [recoveredAccess, setRecoveredAccess] = useState(null);
  const [loading, setLoading] = useState(true);
  const [paying, setPaying] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState(null);
  const [error, setError] = useState('');
  const [routerContext, setRouterContext] = useState({ ip: '', mac: '' });
  const [paymentMethod, setPaymentMethod] = useState('');

  useEffect(() => {
    const routeServiceType = pathServiceType();
    if (routeServiceType) setServiceType(routeServiceType);
    async function load() {
      try {
        const packageUrl = routeServiceType ? `/public/${tenantId}/packages?service_type=${routeServiceType}` : `/public/${tenantId}/packages`;
        const [tenantRes, packagesRes] = await Promise.all([
          publicApi.get(`/public/${tenantId}`),
          publicApi.get(packageUrl),
        ]);
        setTenant(tenantRes.data);
        const methods = Array.isArray(tenantRes.data?.payment_methods) ? tenantRes.data.payment_methods : [];
        setPaymentMethod(methods.find((item) => ['daraja_paybill', 'daraja_buygoods'].includes(item)) || '');
        let loadedPackages = responseItems(packagesRes.data);
        if (routeServiceType && loadedPackages.length === 0) {
          const fallbackRes = await publicApi.get(`/public/${tenantId}/packages`);
          loadedPackages = responseItems(fallbackRes.data);
        }
        setPackages(loadedPackages);
      } catch (err) {
        try {
          const fallbackRes = await publicApi.get(`/public/${tenantId}/packages`);
          const fallbackPackages = responseItems(fallbackRes.data);
          if (fallbackPackages.length > 0) {
            setPackages(fallbackPackages);
            setError('');
            return;
          }
        } catch (_) {
          // Keep the original user-facing error below.
        }
        setError(err.response?.data?.message || 'Unable to load packages');
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [tenantId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const routerIp = params.get('ip');
    const routerMac = params.get('mac');
    setRouterContext({ ip: routerIp || '', mac: routerMac || '' });
    const reference = params.get('reference') || params.get('trxref');
    if (!reference) return;
    // Carried through by the router's hotspot login.html redirect (see
    // hotspot_login_redirect_html) so we know which trapped device to hand
    // the credentials back to after a successful payment.
    async function verify() {
      setVerifying(true);
      try {
        const { data } = await publicApi.get(`/public/${tenantId}/verify?reference=${encodeURIComponent(reference)}`);
        setVerification(data);
        if (data.success) {
          toast.success('Payment verified');
          // If we know which router/device this is (ip came from the
          // captive portal redirect), log the device straight in via
          // MikroTik's built-in login handler instead of leaving the
          // customer to read credentials off the screen and type them in.
          const loginIp = routerIp || data.router_ip;
          if (submitRouterLogin(loginIp, data.username, data.password)) return;
        }
      } catch (err) {
        setVerification({ success: false, message: err.response?.data?.message || 'Payment verification failed. Please contact your ISP.' });
      } finally {
        setVerifying(false);
      }
    }
    verify();
    // routerMac isn't used for the login handoff itself (MikroTik binds the
    // session by ip), but it's kept available here in case it's needed for
    // display/debugging without another URL parse.
    void routerMac;
  }, [tenantId]);

  const openPayment = (pkg, type = serviceType) => {
    setSelectedPackage(pkg);
    setServiceType(type);
    setPhone('');
    setPppoeUsername('');
    setMacAddress('');
  };

  const closePayment = () => {
    if (!paying) {
      setSelectedPackage(null);
      setPhone('');
      setPppoeUsername('');
      setMacAddress('');
    }
  };

  const serviceCopy = {
    hotspot: {
      title: 'Hotspot access',
      description: 'Pay and receive a username/password for this device.',
      icon: Wifi,
    },
    pppoe: {
      title: 'PPPoE access',
      description: 'Enter your PPPoE username. Your account will be reactivated after payment.',
      icon: PlugZap,
    },
    tv: {
      title: 'TV internet',
      description: 'Enter the TV MAC address and pay from this phone or laptop.',
      icon: Monitor,
    },
  };

  const formatDuration = (pkg) => {
    if (pkg?.duration_label) return pkg.duration_label;
    if (pkg?.duration_unit === 'hours') return `${pkg.duration_value || pkg.duration_hours || 1} hours`;
    return `${pkg?.duration_days || 1} days`;
  };

  const pay = async () => {
    if (!phone.trim()) {
      toast.error('Enter your phone number');
      return;
    }
    if (serviceType === 'pppoe' && !pppoeUsername.trim()) {
      toast.error('Enter your PPPoE username');
      return;
    }
    if (serviceType === 'tv' && !macAddress.trim()) {
      toast.error('Enter the TV MAC address');
      return;
    }

    setPaying(true);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/pay`, {
        package_id: selectedPackage.id,
        phone,
        service_type: serviceType,
        username: pppoeUsername,
        mac_address: macAddress,
        ip: routerContext.ip,
        mac: routerContext.mac,
        router_ip: routerContext.ip,
        router_mac: routerContext.mac,
        payment_method: paymentMethod,
      });
      toast.success(data.message || (data.provider === 'mpesa' ? 'Check your phone for the M-Pesa prompt' : 'Redirecting to Paystack'));
      if (data.authorizationUrl) {
        if (!String(data.authorizationUrl).startsWith('https://checkout.paystack.com/')) {
          toast.error('Payment checkout URL was rejected');
          return;
        }
        window.location.href = data.authorizationUrl;
        return;
      }
      if (data.provider === 'mpesa') {
        closePayment();
        return;
      }
      closePayment();
    } catch (err) {
      toast.error(err.response?.data?.message || 'Could not start payment');
    } finally {
      setPaying(false);
    }
  };

  const recover = async (event) => {
    event.preventDefault();
    if (!receiptCode.trim()) {
      toast.error('Enter your payment reference');
      return;
    }

    setRecovering(true);
    setRecoveredAccess(null);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/redeem`, {
        receipt_code: receiptCode,
      });
      setRecoveredAccess(data);
      toast.success('Access restored');
    } catch (err) {
      toast.error(err.response?.data?.message || 'Could not recover access');
    } finally {
      setRecovering(false);
    }
  };

  const loginVoucher = async (event) => {
    event.preventDefault();
    setRecovering(true);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/voucher-login`, { code: voucherCode, router_ip: routerContext.ip });
      setVoucherAccess(data);
      if (!submitRouterLogin(data.router_ip, data.username, data.password)) toast.success('Voucher accepted. Connect through the Hotspot login page.');
    } catch (err) { toast.error(err.response?.data?.message || 'Voucher login failed'); }
    finally { setRecovering(false); }
  };

  const loginAccess = async (event) => {
    event.preventDefault();
    setRecovering(true);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/voucher-login`, { username: accessUsername, password: accessPassword, router_ip: routerContext.ip });
      setVoucherAccess(data);
      if (!submitRouterLogin(data.router_ip, data.username, data.password)) toast.success('Credentials accepted. Connect through the Hotspot login page.');
    } catch (err) { toast.error(err.response?.data?.message || 'Sign-in failed'); }
    finally { setRecovering(false); }
  };

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
        <p className="text-sm font-semibold text-slate-600">Loading packages...</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
        <section className="max-w-md rounded-lg bg-white p-6 text-center shadow-soft ring-1 ring-slate-200">
          <h1 className="text-xl font-bold text-slate-900">Packages unavailable</h1>
          <p className="mt-2 text-sm text-slate-500">{error}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100">
      <section className="bg-[#1e3a5f] px-4 py-10 text-white">
        <div className="mx-auto max-w-6xl">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-md bg-blue-600">
              {tenant?.logo_url ? <img src={tenant.logo_url} alt="" className="h-full w-full rounded-md object-cover" /> : <Wifi size={26} />}
            </div>
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-blue-200">Internet Packages</p>
            <h1 className="text-2xl font-bold sm:text-3xl">{window.location.pathname.startsWith('/pppoe-renew/') ? 'PPPoE Renewal Portal' : (tenant?.business_name || 'Hotspot Portal')}</h1>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-8">
        {(verifying || verification) && (
          <div className={`mb-6 rounded-lg p-4 shadow-soft ring-1 ${verification?.success ? 'bg-green-50 text-green-800 ring-green-100' : 'bg-white text-slate-700 ring-slate-200'}`}>
            {verifying ? (
              <p className="text-sm font-semibold">Verifying payment...</p>
            ) : verification?.success ? (
              <div className="space-y-1 text-sm">
                <p className="font-bold">Payment successful. Your access is ready.</p>
                <p>Package: {verification.package_name}</p>
                {verification.service_type === 'tv' ? (
                  <p>TV MAC: {verification.mac_address || verification.username}</p>
                ) : (
                  <>
                    <p>Username: {verification.username}</p>
                    <p>Password: {verification.password}</p>
                  </>
                )}
                <p>Expires: {verification.expires_at ? new Date(verification.expires_at).toLocaleString() : '-'}</p>
              </div>
            ) : (
              <div className="text-sm">
                <p className="font-bold text-red-700">Payment not verified</p>
                <p>{verification?.message || 'Please contact your ISP for help.'}</p>
              </div>
            )}
          </div>
        )}
          <div className="mb-6 grid gap-4 lg:grid-cols-[1fr_1.2fr]">
          <div className="rounded-lg bg-white p-4 shadow-soft ring-1 ring-slate-200">
            <div className="flex items-center gap-2">
              <CreditCard size={18} className="text-blue-600" />
              <h2 className="font-bold text-slate-900">Buy a package</h2>
            </div>
            <p className="mt-2 text-sm text-slate-500">Choose a package below, then pay securely through Paystack checkout.</p>
          </div>

          <section className="mb-6 rounded-lg bg-white p-4 shadow-soft ring-1 ring-slate-200">
            <h2 className="font-bold text-slate-900">Use a voucher</h2>
            <p className="mt-1 text-sm text-slate-500">Enter the voucher code provided by your provider.</p>
            <form className="mt-3 flex gap-3" onSubmit={loginVoucher}>
              <input className="form-input" placeholder="Voucher code" value={voucherCode} onChange={(event) => setVoucherCode(event.target.value)} />
              <button type="submit" className="btn-primary" disabled={recovering}>Login</button>
            </form>
            <p className="mt-4 text-sm text-slate-500">Already bought a package? Use the username and password sent to you.</p>
            <form className="mt-2 grid gap-3 sm:grid-cols-[1fr_1fr_auto]" onSubmit={loginAccess}>
              <input className="form-input" placeholder="Username" value={accessUsername} onChange={(event) => setAccessUsername(event.target.value)} />
              <input className="form-input" type="password" placeholder="Password" value={accessPassword} onChange={(event) => setAccessPassword(event.target.value)} />
              <button type="submit" className="btn-secondary" disabled={recovering}>Sign in</button>
            </form>
            {voucherAccess && <p className="mt-3 text-sm font-semibold text-emerald-700">Voucher accepted for {voucherAccess.package_name}.</p>}
          </section>

          <form className="rounded-lg bg-white p-4 shadow-soft ring-1 ring-slate-200" onSubmit={recover}>
            <label className="form-label" htmlFor="receiptCode">Already paid? Enter payment reference</label>
            <div className="mt-1 flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <KeyRound className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input
                  id="receiptCode"
                  className="form-input mt-0 pl-10 uppercase"
                  placeholder="e.g. ps_tenant_reference"
                  value={receiptCode}
                  onChange={(event) => setReceiptCode(event.target.value.toUpperCase())}
                />
              </div>
              <button type="submit" className="btn-secondary" disabled={recovering}>
                {recovering ? 'Checking...' : 'Recover Access'}
              </button>
            </div>
            {recoveredAccess && (
              <div className="mt-4 rounded-md bg-green-50 p-3 text-sm text-green-800">
                <p className="font-bold">Access is active</p>
                {recoveredAccess.service_type === 'tv' ? (
                  <p>TV MAC: {recoveredAccess.mac_address || recoveredAccess.username}</p>
                ) : (
                  <>
                    <p>Username: {recoveredAccess.username}</p>
                    <p>Password: {recoveredAccess.password}</p>
                  </>
                )}
                <p>Expires: {new Date(recoveredAccess.expires_at).toLocaleString()}</p>
              </div>
            )}
          </form>
        </div>

        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-bold text-slate-900">All available packages</h2>
            <p className="text-sm text-slate-500">Pick any package offered by {tenant?.business_name || 'this provider'}.</p>
          </div>
          {packages.length > 0 && (
            <p className="text-sm font-semibold text-slate-600">{packages.length} package{packages.length === 1 ? '' : 's'}</p>
          )}
        </div>

        {packages.length === 0 ? (
          <div className="rounded-lg bg-white p-6 text-center shadow-soft ring-1 ring-slate-200">
            <Package className="mx-auto text-slate-400" size={34} />
            <h2 className="mt-3 text-lg font-bold text-slate-900">No packages available</h2>
            <p className="mt-1 text-sm text-slate-500">Please check again later.</p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {packages.map((pkg) => (
              <article key={pkg.id} className="rounded-lg bg-white p-5 shadow-soft ring-1 ring-slate-200">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-lg font-bold text-slate-900">{pkg.name}</h2>
                    <p className="mt-1 text-sm text-slate-500">{pkg.speed}</p>
                  </div>
                  <div className="rounded-md bg-blue-50 p-2 text-blue-600">
                    {packageType(pkg) === 'pppoe' ? <PlugZap size={20} /> : <Wifi size={20} />}
                  </div>
                </div>
                <p className="mt-5 text-3xl font-bold text-slate-900">KES {pkg.price}</p>
                <p className="mt-1 text-sm text-slate-500">{formatDuration(pkg)} access</p>
                <div className="mt-5 grid gap-2">
                  {[packageType(pkg), ...(packageType(pkg) === 'hotspot' ? ['tv'] : [])].map((key) => {
                    const item = serviceCopy[key];
                    const Icon = item.icon;
                    return (
                      <button
                        key={key}
                        type="button"
                        className={key === packageType(pkg) ? 'btn-primary w-full' : 'btn-secondary w-full justify-center'}
                        onClick={() => openPayment(pkg, key)}
                      >
                        <Icon size={18} />
                        {key === 'hotspot' ? 'Pay Hotspot' : key === 'pppoe' ? 'Pay PPPoE' : 'Pay TV MAC'}
                      </button>
                    );
                  })}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {selectedPackage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
          <section className="w-full max-w-md rounded-lg bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 className="text-lg font-bold text-slate-900">{serviceCopy[serviceType].title}</h2>
                <p className="text-sm text-slate-500">{selectedPackage.name} - KES {selectedPackage.price} for {formatDuration(selectedPackage)}</p>
              </div>
              <button type="button" className="rounded-md p-2 text-slate-500 hover:bg-slate-100" onClick={closePayment} aria-label="Close payment">
                <X size={20} />
              </button>
            </div>
            <div className="p-5">
              <p className="mb-4 text-sm text-slate-600">{serviceCopy[serviceType].description}</p>
              {serviceType === 'pppoe' && (
                <div className="mb-4">
                  <label className="form-label" htmlFor="pppoeUsername">PPPoE username</label>
                  <div className="relative mt-1">
                    <PlugZap className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                    <input
                      id="pppoeUsername"
                      className="form-input mt-0 pl-10"
                      placeholder="Your PPPoE username"
                      value={pppoeUsername}
                      onChange={(event) => setPppoeUsername(event.target.value)}
                    />
                  </div>
                </div>
              )}
            
              {serviceType === 'tv' && (
                <div className="mb-4">
                  <label className="form-label" htmlFor="macAddress">TV MAC address</label>
                  <div className="relative mt-1">
                    <Monitor className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                    <input
                      id="macAddress"
                      className="form-input mt-0 pl-10 uppercase"
                      placeholder="AA:BB:CC:DD:EE:FF"
                      value={macAddress}
                      onChange={(event) => setMacAddress(event.target.value.toUpperCase())}
                    />
                  </div>
                </div>
              )}
              <label className="form-label" htmlFor="phone">Phone number</label>
              <div className="relative mt-1">
                <Phone className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input
                  id="phone"
                  className="form-input mt-0 pl-10"
                  placeholder="2547XXXXXXXX"
                  value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                />
              </div>
              <button type="button" className="btn-primary mt-5 w-full" onClick={pay} disabled={paying}>
                <CreditCard size={18} />
                {paying ? 'Opening checkout...' : 'Continue to Paystack'}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
