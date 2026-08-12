import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import Navbar from './components/Navbar';
import AdminLayout from './components/admin/AdminLayout';
import AdminProtectedRoute from './components/admin/AdminProtectedRoute';
import ProtectedRoute from './components/ProtectedRoute';
import Sidebar from './components/Sidebar';
import AdminAuditLog from './pages/admin/AdminAuditLog';
import AdminDashboard from './pages/admin/AdminDashboard';
import AdminLogin from './pages/admin/AdminLogin';
import AdminSiteSettings from './pages/admin/AdminSiteSettings';
import AdminSubscriptions from './pages/admin/AdminSubscriptions';
import AdminSystem from './pages/admin/AdminSystem';
import AdminTenantDetail from './pages/admin/AdminTenantDetail';
import AdminTenants from './pages/admin/AdminTenants';
import AdminUsers from './pages/admin/AdminUsers';
import BusinessSettings from './pages/BusinessSettings';
import Customers from './pages/Customers';
import CustomerPortal from './pages/CustomerPortal';
import Dashboard from './pages/Dashboard';
import Emails from './pages/Emails';
import Equipment from './pages/Equipment';
import Expenses from './pages/Expenses';
import Home from './pages/Home';
import IspOperations from './pages/IspOperations';
import Login from './pages/Login';
import Messages from './pages/Messages';
import MikrotikLink from './pages/MikrotikLink';
import MikrotikSettings from './pages/MikrotikSettings';
import NotFound from './pages/NotFound';
import Packages from './pages/Packages';
import Payments from './pages/Payments';
import Profile from './pages/Profile';
import PppoeProfile from './pages/PppoeProfile';
import Register from './pages/Register';
import Reports from './pages/Reports';
import Vouchers from './pages/Vouchers';
import { ADMIN_DASHBOARD_PATH, ADMIN_LOGIN_PATH, ADMIN_PATH, adminPath } from './config/adminPaths';
import { TENANT_THEME_EVENT, applyTenantTheme } from './utils/theme';

function DashboardLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    applyTenantTheme();
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const listener = () => applyTenantTheme();
    media.addEventListener('change', listener);
    window.addEventListener('storage', listener);
    window.addEventListener(TENANT_THEME_EVENT, listener);
    return () => {
      media.removeEventListener('change', listener);
      window.removeEventListener('storage', listener);
      window.removeEventListener(TENANT_THEME_EVENT, listener);
    };
  }, []);

  return (
    <div className="tenant-app theme-page min-h-screen w-full">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <div className="min-h-screen w-full min-w-0 lg:ml-[232px] lg:w-[calc(100%-232px)]">
        <Navbar onMenuClick={() => setSidebarOpen(true)} />
        <main className="mx-auto w-full max-w-[1200px] px-4 py-4 sm:px-8 sm:py-5">
          <Routes>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/customers" element={<Customers title="Users" />} />
            <Route path="/pppoe-customers" element={<Customers initialFilter="pppoe" serviceLocked="pppoe" title="PPPoE Customers" />} />
            <Route path="/active-users" element={<Navigate to="/customers" replace />} />
            
            <Route path="/tickets" element={<IspOperations module="tickets" />} />
            <Route path="/packages" element={<Packages />} />
            <Route path="/payments" element={<Payments />} />
            <Route path="/vouchers" element={<Vouchers />} />
            <Route path="/expenses" element={<Expenses />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/messages" element={<Messages />} />
            <Route path="/emails" element={<Emails />} />
            <Route path="/mikrotik" element={<MikrotikSettings />} />
            <Route path="/mikrotik/link" element={<MikrotikLink />} />
            <Route path="/equipment" element={<Equipment />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/settings" element={<BusinessSettings />} />
            <Route path="/settings/expresswifi/edit" element={<BusinessSettings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const isAdminHost = window.location.hostname.split('.')[0] === 'admin';

  return (
    <Routes>
      <Route path="/" element={isAdminHost ? <Navigate to={ADMIN_LOGIN_PATH} replace /> : <Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/portal/:tenantId" element={<CustomerPortal />} />
      <Route path="/customers/:tenantId" element={<CustomerPortal />} />
      <Route path="/customer/:tenantId" element={<CustomerPortal />} />
      <Route path="/hotspot/:tenantId" element={<CustomerPortal />} />
      <Route path="/pppoe/:tenantId" element={<CustomerPortal />} />
      <Route path="/pppoe-renew/:tenantId" element={<CustomerPortal />} />
      <Route path="/pppoe-profile/:tenantId" element={<PppoeProfile />} />
      <Route path="/pppoe-expired/:tenantId" element={<PppoeProfile />} />
      <Route path="/tv/:tenantId" element={<CustomerPortal />} />
      <Route path={`${ADMIN_PATH}/login`} element={<AdminLogin />} />
      <Route element={<AdminProtectedRoute />}>
        <Route path={ADMIN_PATH} element={<AdminLayout />}>
          <Route index element={<Navigate to={ADMIN_DASHBOARD_PATH} replace />} />
          <Route path="dashboard" element={<AdminDashboard />} />
          <Route path="tenants" element={<AdminTenants />} />
          <Route path="tenants/:id" element={<AdminTenantDetail />} />
          <Route path="subscriptions" element={<AdminSubscriptions />} />
          <Route path="users" element={<AdminUsers />} />
          <Route path="site" element={<AdminSiteSettings />} />
          <Route path="system" element={<AdminSystem />} />
          <Route path="audit" element={<AdminAuditLog />} />
        </Route>
      </Route>
      {ADMIN_PATH !== '/admin' && (
        <>
          <Route path="/admin/login" element={<Navigate to={ADMIN_LOGIN_PATH} replace />} />
          <Route path="/admin" element={<Navigate to={adminPath('dashboard')} replace />} />
        </>
      )}
      <Route element={<ProtectedRoute />}>
        <Route path="/*" element={<DashboardLayout />} />
      </Route>
    </Routes>
  );
}
