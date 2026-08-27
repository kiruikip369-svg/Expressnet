import {
  Bell,
  Building2,
  ChevronDown,
  Home,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Shield,
  UserCircle,
  Users,
  Wifi,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAdminAuth } from '../../context/AdminAuthContext';
import { ADMIN_LOGIN_PATH, adminPath } from '../../config/adminPaths';

const links = [
  { to: adminPath('dashboard'), label: 'Dashboard', icon: Home },
  { to: adminPath('tenants'), label: 'Tenants', icon: Users },
  { to: adminPath('users'), label: 'Customers', icon: Building2 },
  { to: adminPath('system'), label: 'Reports', icon: LayoutDashboard },
  { to: adminPath('audit'), label: 'Audit Logs', icon: Shield },
  { to: adminPath('users'), label: 'Users & Roles', icon: UserCircle },
];

export default function AdminLayout() {
  const [open, setOpen] = useState(false);
  const { admin, logoutAdmin } = useAdminAuth();
  const navigate = useNavigate();

  const logout = () => {
    logoutAdmin();
    navigate(ADMIN_LOGIN_PATH, { replace: true });
  };

  const navClass = ({ isActive }) =>
    [
      'flex h-10 items-center gap-3 rounded-md px-3 text-[13px] font-semibold transition',
      isActive ? 'bg-[#0d9bf2] text-white shadow-[0_12px_24px_rgba(13,155,242,0.25)]' : 'text-blue-100 hover:bg-white/10 hover:text-white',
    ].join(' ');

  return (
    <div className="min-h-screen bg-[#f6f9fd] text-[#0f2240]">
      <div
        className={`fixed inset-0 z-30 bg-slate-950/50 lg:hidden ${open ? '' : 'hidden'}`}
        onClick={() => setOpen(false)}
      />

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[260px] flex-col bg-gradient-to-b from-[#061d3d] via-[#072b5c] to-[#051a35] px-4 py-5 text-white transition-transform lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="mb-8 flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[#11a8ff] shadow-[0_10px_24px_rgba(17,168,255,0.35)]">
              <Wifi size={20} />
            </div>
            <h1 className="truncate text-lg font-bold text-[#19b5ff]">ExpressNet Mtandao</h1>
          </div>
          <button className="rounded-md p-2 hover:bg-white/10 lg:hidden" onClick={() => setOpen(false)} aria-label="Close admin nav">
            <X size={20} />
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto pr-1">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink key={`${to}-${label}`} to={to} className={navClass} onClick={() => setOpen(false)}>
              <Icon size={16} />
              <span className="flex-1">{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-white/10 pt-4">
          <div className="mb-3 flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#159fff]">
              <UserCircle size={27} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-bold">{admin?.name || 'Super Admin'}</p>
              <p className="truncate text-[11px] text-blue-100">{admin?.email || 'admin@expressnet.co.ke'}</p>
            </div>
            <ChevronDown size={15} className="text-blue-100" />
          </div>
          <button type="button" className="flex h-9 w-full items-center gap-3 rounded-md px-3 text-[13px] font-semibold text-blue-100 hover:bg-white/10 hover:text-white" onClick={logout}>
            <LogOut size={16} />
            Logout
          </button>
        </div>
      </aside>

      <div className="min-w-0 lg:ml-[260px]">
        <header className="sticky top-0 z-20 flex h-[72px] items-center justify-between border-b border-slate-100 bg-white px-4 shadow-[0_8px_30px_rgba(15,34,64,0.04)] lg:px-8">
          <div className="flex min-w-0 items-center gap-4">
            <button className="rounded-md p-2 text-[#173b66] hover:bg-slate-100 lg:hidden" onClick={() => setOpen(true)} aria-label="Open admin nav">
              <Menu size={22} />
            </button>
            <button className="hidden rounded-md p-2 text-[#173b66] hover:bg-slate-100 lg:block" aria-label="Toggle menu">
              <Menu size={22} />
            </button>
            <div>
              <h2 className="text-lg font-bold text-[#102347]">Dashboard</h2>
              <div className="mt-1 flex items-center gap-2 text-[11px] font-medium text-slate-400">
                <span>Home</span>
                <span>/</span>
                <span className="text-[#1d75d8]">Dashboard</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <label className="relative hidden w-[270px] md:block">
              <Search size={15} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                className="h-10 w-full rounded-md border border-slate-200 bg-white pl-11 pr-3 text-[12px] outline-none transition placeholder:text-slate-400 focus:border-[#0d9bf2] focus:ring-2 focus:ring-blue-100"
                placeholder="Search tenants, invoices, users..."
              />
            </label>
            <button className="relative flex h-10 w-10 items-center justify-center rounded-full text-[#173b66] hover:bg-slate-100" aria-label="Notifications">
              <Bell size={20} />
              <span className="absolute right-2 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[#0d9bf2] px-1 text-[9px] font-bold text-white">5</span>
            </button>
            <div className="hidden items-center gap-3 md:flex">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#0d9bf2] text-white">
                <UserCircle size={25} />
              </div>
              <div>
                <p className="text-[12px] font-bold text-[#102347]">{admin?.name || 'Admin User'}</p>
                <p className="text-[11px] text-slate-400">{admin?.role || 'Super Admin'}</p>
              </div>
              <ChevronDown size={16} className="text-[#173b66]" />
            </div>
          </div>
        </header>

        <main className="px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
