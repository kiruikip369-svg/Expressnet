import { ChevronRight, CreditCard, KeyRound, Monitor, Package, Phone, PlugZap, Wifi, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { useParams } from 'react-router-dom';
import axios from 'axios';

const publicApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  headers: { 'X-Requested-With': 'XMLHttpRequest' },
});

function submitRouterLogin(routerIp, username, password, linkLogin = '', dst = '') {
  if ((!routerIp && !linkLogin) || !username || !password) return false;
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = linkLogin || `http://${routerIp}/login`;
  form.style.display = 'none';
  [
    ['username', username],
    ['password', password],
    ['dst', dst || 'http://connectivitycheck.gstatic.com/generate_204'],
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

function preferredDarajaMethod(methods = []) {
  return methods.find((item) => ['daraja_paybill', 'daraja_buygoods'].includes(item)) || 'daraja_paybill';
}

export default function CustomerPortal() {
  const { tenantId } = useParams();
  const [tenant, setTenant] = useState(null);
  const [packages, setPackages] = useState([]);
  const [phone, setPhone] = useState('');
  const [customerName, setCustomerName] = useState('');
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
  const [routerContext, setRouterContext] = useState({ ip: '', mac: '', linkLogin: '', dst: '' });
  const [paymentMethod, setPaymentMethod] = useState('');
  const [pendingPaymentId, setPendingPaymentId] = useState('');

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
        setPaymentMethod(preferredDarajaMethod(methods));
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
    const routerIp = params.get('router_ip') || params.get('ip');
    const routerMac = params.get('mac');
    const linkLogin = params.get('link_login') || params.get('link-login') || '';
    const dst = params.get('dst') || params.get('link-orig') || '';
    setRouterContext({ ip: routerIp || '', mac: routerMac || '', linkLogin, dst });
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
          if (submitRouterLogin(loginIp, data.username, data.password, linkLogin || data.link_login, dst || data.dst)) return;
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

  useEffect(() => {
    if (!pendingPaymentId) return undefined;
    let stopped = false;
    let attempts = 0;

    async function verifyMpesaPayment() {
      attempts += 1;
      setVerifying(true);
      try {
        const { data } = await publicApi.get(`/public/${tenantId}/verify?payment_id=${encodeURIComponent(pendingPaymentId)}`);
        if (stopped) return;
        setVerification(data);
        if (data.success) {
          setPendingPaymentId('');
          toast.success('Payment verified');
          submitRouterLogin(routerContext.ip || data.router_ip, data.username, data.password, routerContext.linkLogin || data.link_login, routerContext.dst || data.dst);
        } else if (attempts >= 18) {
          setPendingPaymentId('');
        }
      } catch (err) {
        if (stopped) return;
        if (attempts >= 18) {
          setPendingPaymentId('');
          setVerification({ success: false, message: err.response?.data?.message || 'Payment verification is still pending. Please contact your ISP if this continues.' });
        }
      } finally {
        if (!stopped) setVerifying(false);
      }
    }

    verifyMpesaPayment();
    const timer = window.setInterval(verifyMpesaPayment, 5000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [pendingPaymentId, routerContext.dst, routerContext.ip, routerContext.linkLogin, tenantId]);

  const openPayment = (pkg, type = serviceType) => {
    setSelectedPackage(pkg);
    setServiceType(type);
    setPhone('');
    setCustomerName('');
    setPppoeUsername('');
    setMacAddress('');
  };

  const closePayment = () => {
    if (!paying) {
      setSelectedPackage(null);
      setPhone('');
      setCustomerName('');
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
      description: 'Enter your PPPoE username to renew, or leave it blank to get a new account after payment.',
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
    if (serviceType === 'tv' && !macAddress.trim()) {
      toast.error('Enter the TV MAC address');
      return;
    }

    setPaying(true);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/pay`, {
        package_id: selectedPackage.id,
        phone,
        customer_name: customerName,
        service_type: serviceType,
        username: pppoeUsername,
        mac_address: macAddress,
        ip: routerContext.ip,
        mac: routerContext.mac,
        router_ip: routerContext.ip,
        router_mac: routerContext.mac,
        link_login: routerContext.linkLogin,
        dst: routerContext.dst,
        payment_method: paymentMethod,
      });
      toast.success(data.message || 'Check your phone for the M-Pesa prompt');
      if (data.paymentId) {
        setVerification({ success: false, status: 'pending', message: 'Waiting for M-Pesa confirmation.' });
        setPendingPaymentId(data.paymentId);
      }
      setSelectedPackage(null);
      setPhone('');
      setCustomerName('');
      setPppoeUsername('');
      setMacAddress('');
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
        router_ip: routerContext.ip,
        link_login: routerContext.linkLogin,
        dst: routerContext.dst,
      });
      setRecoveredAccess(data);
      if (data.success && submitRouterLogin(data.router_ip || routerContext.ip, data.username, data.password, data.link_login || routerContext.linkLogin, data.dst || routerContext.dst)) return;
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
      const { data } = await publicApi.post(`/public/${tenantId}/voucher-login`, { code: voucherCode, router_ip: routerContext.ip, link_login: routerContext.linkLogin, dst: routerContext.dst });
      setVoucherAccess(data);
      if (!submitRouterLogin(data.router_ip, data.username, data.password, data.link_login || routerContext.linkLogin, data.dst || routerContext.dst)) toast.success('Voucher accepted. Connect through the Hotspot login page.');
    } catch (err) { toast.error(err.response?.data?.message || 'Voucher code is wrong or expired'); }
    finally { setRecovering(false); }
  };

  const loginAccess = async (event) => {
    event.preventDefault();
    setRecovering(true);
    try {
      const { data } = await publicApi.post(`/public/${tenantId}/voucher-login`, { username: accessUsername, password: accessPassword, router_ip: routerContext.ip, link_login: routerContext.linkLogin, dst: routerContext.dst });
      setVoucherAccess(data);
      if (!submitRouterLogin(data.router_ip, data.username, data.password, data.link_login || routerContext.linkLogin, data.dst || routerContext.dst)) toast.success('Credentials accepted. Connect through the Hotspot login page.');
    } catch (err) { toast.error(err.response?.data?.message || 'Username or password is wrong'); }
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
    <main className="min-h-screen bg-black text-white">
      <section className="px-3 pt-3 sm:px-5">
        <div className="mx-auto max-w-3xl rounded-b-2xl rounded-t-lg bg-[#2600d8] px-5 py-4 text-center shadow-[0_18px_38px_rgba(38,0,216,0.28)] sm:py-5">
          <div className="mx-auto flex h-14 w-20 items-center justify-center overflow-hidden rounded-lg bg-white/15">
            {tenant?.logo_url ? <img src={tenant.logo_url} alt="" className="h-full w-full object-cover" /> : <Wifi size={24} />}
          </div>
          <h1 className="mt-3 text-lg font-bold leading-tight sm:text-xl">{window.location.pathname.startsWith('/pppoe-renew/') ? 'PPPoE Renewal' : (tenant?.business_name || 'Hotspot Portal')}</h1>
          <div className="mt-3 flex items-center justify-center gap-2 text-sm font-bold">
            <span>Select</span>
            <ChevronRight size={16} className="text-white/75" />
            <span>Pay</span>
            <ChevronRight size={16} className="text-white/75" />
            <span>Connect</span>
          </div>
          <a href={`tel:${tenant?.phone || tenant?.support_phone || ''}`} className="mx-auto mt-4 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-black/28 px-5 text-base font-bold tracking-wide text-white">
            <Phone size={18} />
            {tenant?.phone || tenant?.support_phone || '0797443584'}
          </a>
        </div>
      </section>

      <section className="mx-auto max-w-3xl px-5 py-6">
        {(verifying || verification) && (
          <div className={`mb-5 rounded-lg p-4 shadow-soft ring-1 ${verification?.success ? 'bg-green-950 text-green-100 ring-green-700' : 'bg-[#252525] text-slate-200 ring-white/10'}`}>
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
        <div className="mb-6 grid gap-3">
          <section className="rounded-lg border border-white/10 bg-[#242424] p-4 shadow-[0_10px_26px_rgba(0,0,0,0.35)]">
            <div className="flex items-center gap-2">
              <CreditCard size={17} className="text-white" />
              <h2 className="text-base font-bold text-white">Quick access</h2>
            </div>
            <p className="mt-1 text-xs text-slate-300">Use a voucher, M-Pesa code, or your username and password.</p>

            <form className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]" onSubmit={loginVoucher}>
              <input className="h-10 rounded-md border border-white/10 bg-black px-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-[#2600d8]" placeholder="Voucher code" value={voucherCode} onChange={(event) => setVoucherCode(event.target.value)} />
              <button type="submit" className="inline-flex h-10 items-center justify-center rounded-md bg-[#2600d8] px-5 text-sm font-bold text-white shadow-lg shadow-black/30" disabled={recovering}>Login</button>
            </form>

            <form className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]" onSubmit={recover}>
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={17} />
                <input
                  id="receiptCode"
                  className="h-10 w-full rounded-md border border-white/10 bg-black px-3 pl-10 text-sm uppercase text-white outline-none placeholder:text-slate-500 focus:border-[#2600d8]"
                  placeholder="M-Pesa code"
                  value={receiptCode}
                  onChange={(event) => setReceiptCode(event.target.value.toUpperCase())}
                />
              </div>
              <button type="submit" className="inline-flex h-10 items-center justify-center rounded-md border border-[#2600d8] px-5 text-sm font-bold text-white" disabled={recovering}>
                {recovering ? 'Checking...' : 'Connect'}
              </button>
            </form>

            <form className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={loginAccess}>
              <input className="h-10 rounded-md border border-white/10 bg-black px-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-[#2600d8]" placeholder="Username" value={accessUsername} onChange={(event) => setAccessUsername(event.target.value)} />
              <input className="h-10 rounded-md border border-white/10 bg-black px-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-[#2600d8]" type="password" placeholder="Password" value={accessPassword} onChange={(event) => setAccessPassword(event.target.value)} />
              <button type="submit" className="inline-flex h-10 items-center justify-center rounded-md border border-[#2600d8] px-5 text-sm font-bold text-white" disabled={recovering}>Sign in</button>
            </form>

            {voucherAccess && <p className="mt-3 text-sm font-semibold text-emerald-400">Voucher accepted for {voucherAccess.package_name}.</p>}
            {recoveredAccess && (
              <div className="mt-4 rounded-md bg-green-950 p-3 text-sm text-green-100">
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
          </section>
        </div>

        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-bold text-white">Unlimited packages</h2>
            <p className="text-sm text-slate-400">Pick any package offered by {tenant?.business_name || 'this provider'}.</p>
          </div>
          {packages.length > 0 && (
            <p className="text-sm font-semibold text-slate-400">{packages.length} package{packages.length === 1 ? '' : 's'}</p>
          )}
        </div>

        {packages.length === 0 ? (
          <div className="rounded-lg border border-white/10 bg-[#242424] p-6 text-center shadow-soft">
            <Package className="mx-auto text-slate-400" size={34} />
            <h2 className="mt-3 text-lg font-bold text-white">No packages available</h2>
            <p className="mt-1 text-sm text-slate-400">Please check again later.</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {packages.map((pkg) => (
              <article key={pkg.id} className="flex min-h-[92px] items-center justify-between gap-4 rounded-lg border border-white/10 bg-[#242424] p-5 shadow-[0_10px_24px_rgba(0,0,0,0.36)]">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="break-words text-lg font-extrabold uppercase leading-snug text-white">{pkg.name}</h2>
                    {packageType(pkg) === 'pppoe' ? <PlugZap className="hidden shrink-0 text-slate-400 sm:block" size={18} /> : <Wifi className="hidden shrink-0 text-slate-400 sm:block" size={18} />}
                  </div>
                  <p className="mt-1 text-base text-slate-300"><span className="font-bold text-white">Ksh {pkg.price}</span> for {formatDuration(pkg)}</p>
                  {pkg.speed && <p className="mt-1 text-xs text-slate-500">{pkg.speed}</p>}
                </div>
                <button
                  type="button"
                  className="inline-flex h-11 shrink-0 items-center justify-center rounded-md bg-[#2600d8] px-6 text-base font-bold text-white shadow-[0_12px_22px_rgba(0,0,0,0.45)]"
                  onClick={() => openPayment(pkg, packageType(pkg))}
                >
                  Buy
                </button>
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
                  <label className="form-label" htmlFor="pppoeUsername">PPPoE username optional</label>
                  <div className="relative mt-1">
                    <PlugZap className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                    <input
                      id="pppoeUsername"
                      className="form-input mt-0 pl-10"
                      placeholder="Leave blank for a new account"
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
              <label className="form-label" htmlFor="customerName">Full name</label>
              <input
                id="customerName"
                className="form-input mb-4"
                placeholder="Customer name"
                value={customerName}
                onChange={(event) => setCustomerName(event.target.value)}
              />
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
                {paying ? (paymentMethod ? 'Sending STK push...' : 'Opening checkout...') : (paymentMethod ? 'Send M-Pesa Prompt' : 'Continue to Checkout')}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
