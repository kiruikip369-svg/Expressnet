import {
  BriefcaseBusiness,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  Database,
  FileBarChart,
  FileText,
  Gauge,
  HardHat,
  LayoutDashboard,
  Network,
  Package,
  RadioTower,
  Receipt,
  Settings,
  ShoppingBag,
  Ticket,
  Users,
  WalletCards,
  Wifi,
  Wrench,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { canAccessPage, pageForPath } from '../utils/permissions';

const sections = [
  {
    links: [{ to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard }],
  },
  {
    title: 'Customers',
    icon: Users,
    links: [
      { to: '/pppoe-customers', label: 'PPPoE', icon: RadioTower },
      { to: '/hotspot-customers', label: 'Hotspot', icon: Wifi },
      { to: '/static-customers', label: 'Static', icon: Database },
    ],
  },
  {
    title: 'Management',
    icon: BriefcaseBusiness,
    links: [
      { to: '/tickets', label: 'Tasks', icon: Ticket },
      { to: '/packages', label: 'Packages', icon: Package },
      { to: '/vouchers', label: 'Vouchers', icon: WalletCards },
      { to: '/reports/management', label: 'Management Report', icon: FileBarChart },
    ],
  },
  {
    title: 'Finance',
    icon: CreditCard,
    links: [
      { to: '/payments', label: 'Payments', icon: CreditCard },
      { to: '/expenses', label: 'Expenses', icon: Receipt },
      { to: '/expenses', label: 'Salary', icon: ShoppingBag },
      { to: '/payments', label: 'Invoices', icon: FileText },
      { to: '/reports/finance', label: 'Financial Report', icon: FileBarChart },
    ],
  },
  {
    title: 'Network',
    icon: Network,
    links: [
      { to: '/mikrotik', label: 'Mikrotik', icon: Gauge },
      { to: '/mikrotik/link', label: 'TR-069', icon: Gauge },
      { to: '/reports/network', label: 'Network Report', icon: RadioTower },
    ],
  },
  {
    title: 'Tools / Equipments',
    icon: Wrench,
    links: [
      { to: '/equipment', label: 'Report', icon: FileBarChart },
      { to: '/equipment', label: 'Requisitions', icon: HardHat },
    ],
  },
  {
    links: [{ to: '/settings', label: 'Settings', icon: Settings, trailing: ChevronLeft }],
  },
];

function tenantInitial(tenant) {
  const name = tenant?.business_name || tenant?.name || 'Expressnet';
  return String(name).trim().charAt(0).toUpperCase() || 'E';
}

export default function Sidebar({ open, onClose }) {
  const { tenant } = useAuth();
  const location = useLocation();
  const [collapsedSections, setCollapsedSections] = useState({});

  const visibleLinks = (links) => links.filter(({ to }) => canAccessPage(tenant, pageForPath(to)));

  const isGroupActive = (links) => links.some((link) => {
    if (link.to === '/dashboard') return location.pathname === '/dashboard';
    return location.pathname === link.to || location.pathname.startsWith(`${link.to}/`);
  });

  const toggleSection = (title) => {
    setCollapsedSections((current) => ({ ...current, [title]: !(current[title] ?? false) }));
  };

  const navClass = ({ isActive }) =>
    [
      'flex h-9 items-center gap-3 rounded-md px-3 text-[13px] font-medium transition',
      isActive
        ? 'bg-[var(--sidebar-active)] text-white shadow-[inset_3px_0_0_rgba(255,255,255,0.28)]'
        : 'text-white/88 hover:bg-white/10 hover:text-white',
    ].join(' ');

  return (
    <>
      <div
        className={`fixed inset-0 z-30 bg-slate-950/40 transition-opacity lg:hidden ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
      />

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col px-3 py-4 text-white shadow-2xl transition-transform lg:w-[232px] lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
        style={{ background: 'linear-gradient(180deg, var(--sidebar-top) 0%, var(--sidebar-middle) 46%, var(--sidebar-bottom) 100%)' }}
      >
        <div className="mb-4 flex h-12 items-start justify-between px-1">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wide text-white/78">Expressnet Billing</p>
            <h1 className="mt-1 text-lg font-bold leading-tight text-white">Tenant Portal</h1>
          </div>
          <button
            type="button"
            className="rounded-md p-2 text-white/85 hover:bg-white/10"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={20} className="lg:hidden" />
            <ChevronLeft size={19} className="hidden lg:block" />
          </button>
        </div>

        <nav className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {sections.map((section, index) => {
            const links = visibleLinks(section.links);
            if (!links.length) return null;
            const SectionIcon = section.icon;
            const groupActive = isGroupActive(links);
            const expanded = collapsedSections[section.title] ?? false;

            if (!section.title) {
              return (
                <div key={`single-${index}`} className={index ? 'border-t border-white/10 pt-3' : ''}>
                  {links.map(({ to, label, icon: Icon, trailing: Trailing }) => (
                    <NavLink key={`${label}-${to}`} to={to} className={navClass} onClick={onClose}>
                      <Icon size={17} strokeWidth={2.1} />
                      <span className="min-w-0 flex-1 truncate">{label}</span>
                      {Trailing && <Trailing size={15} />}
                    </NavLink>
                  ))}
                </div>
              );
            }

            return (
              <div key={section.title} className="border-t border-white/10 pt-3 first:border-t-0 first:pt-0">
                <button
                  type="button"
                  className={`flex h-8 w-full items-center gap-3 rounded-md px-3 text-left text-[13px] font-medium transition hover:bg-white/10 ${groupActive ? 'text-white' : 'text-white/88'}`}
                  onClick={() => toggleSection(section.title)}
                  aria-expanded={expanded}
                >
                  {SectionIcon && <SectionIcon size={16} strokeWidth={2.1} />}
                  <span className="min-w-0 flex-1 truncate">{section.title}</span>
                  {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                </button>
                {expanded && (
                  <div className="mt-1 space-y-0.5">
                    {links.map(({ to, label, icon: Icon }) => (
                      <NavLink
                        key={`${section.title}-${label}-${to}`}
                        to={to}
                        className={({ isActive }) => [
                          'ml-7 flex h-7 items-center gap-2 rounded-md px-2 text-[13px] font-normal transition',
                          isActive ? 'bg-[var(--sidebar-active)] text-white' : 'text-white/84 hover:bg-white/10 hover:text-white',
                        ].join(' ')}
                        onClick={onClose}
                      >
                        <Icon size={13} strokeWidth={2.2} />
                        <span className="min-w-0 flex-1 truncate">{label}</span>
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="mt-4 rounded-md border p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" style={{ background: 'color-mix(in srgb, var(--sidebar-bottom) 28%, transparent)', borderColor: 'color-mix(in srgb, var(--sidebar-border) 32%, transparent)' }}>
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white shadow-sm" style={{ background: 'var(--sidebar-avatar)' }}>
              {tenantInitial(tenant)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-bold text-white">{tenant?.business_name || tenant?.name || 'Expressnet'}</p>
              <p className="truncate text-[11px] text-white/70">Tenant ID: {tenant?.id || 'TEN-001'}</p>
            </div>
            <ChevronDown size={15} className="text-white/75" />
          </div>
        </div>
      </aside>
    </>
  );
}
