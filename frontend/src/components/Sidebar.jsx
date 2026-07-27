import {
  CreditCard,
  Gauge,
  LayoutDashboard,
  Mail,
  MessageSquare,
  Package,
  BarChart2,
  Receipt,
  Router,
  Settings,
  Ticket,
  Users,
  WalletCards,
  X,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';

const sections = [
  {
    links: [{ to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard }],
  },
  {
    title: 'Users',
    links: [
      { to: '/customers', label: 'Users', icon: Users },
      { to: '/pppoe-customers', label: 'Customers', icon: CreditCard },
      { to: '/tickets', label: 'Tickets', icon: Ticket },
    ],
  },
  {
    title: 'Finance',
    links: [
      { to: '/packages', label: 'Packages', icon: Package },
      { to: '/payments', label: 'Payments', icon: CreditCard },
      { to: '/vouchers', label: 'Vouchers', icon: WalletCards },
      { to: '/expenses', label: 'Expenses', icon: Receipt },
      { to: '/reports', label: 'Reports', icon: BarChart2 },
    ],
  },
  {
    title: 'Communication',
    links: [
      { to: '/messages', label: 'Messages', icon: MessageSquare },
      { to: '/emails', label: 'Emails', icon: Mail },
    ],
  },
  {
    title: 'Devices',
    links: [
      { to: '/mikrotik', label: 'MikroTik', icon: Router },
      { to: '/equipment', label: 'Equipment', icon: Gauge },
    ],
  },
  {
    title: 'System',
    links: [
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
];

export default function Sidebar({ open, onClose }) {
  const navClass = ({ isActive }) =>
    [
      'flex h-8 items-center gap-3 rounded-md px-3 text-xs font-normal transition',
      isActive ? 'bg-app-accent text-white' : 'text-slate-200 hover:bg-white/10 hover:text-white',
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
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-sidebar px-4 py-5 text-white transition-transform lg:w-[240px] lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="mb-6 flex h-10 items-center justify-between">
          <div>
            <p className="text-[10px] font-normal uppercase tracking-wide text-blue-200">Billing SaaS</p>
            <h1 className="text-sm font-normal text-white">Tenant Portal</h1>
          </div>
          <button
            type="button"
            className="rounded-md p-2 text-slate-200 hover:bg-white/10 lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="space-y-5 overflow-y-auto">
          {sections.map((section, sectionIndex) => (
            <div key={section.title || 'main'} className={sectionIndex ? 'border-t border-white/15 pt-4' : ''}>
              {section.title && (
                <p className="mb-2 px-3 text-[10px] font-normal uppercase tracking-wide text-white/60">{section.title}</p>
              )}
              <div className="space-y-1">
                {section.links.map(({ to, label, icon: Icon }) => (
                  <NavLink key={to} to={to} className={navClass} onClick={onClose}>
                    <Icon size={16} />
                    <span className="min-w-0 flex-1 truncate">{label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
      </aside>
    </>
  );
}
