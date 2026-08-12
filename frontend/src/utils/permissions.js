export const PAGE_PERMISSIONS = {
  dashboard: ['/dashboard'],
  customers: ['/customers', '/pppoe-customers', '/hotspot-customers', '/static-customers', '/active-users'],
  tickets: ['/tickets'],
  packages: ['/packages'],
  payments: ['/payments'],
  vouchers: ['/vouchers'],
  requisitions: ['/requisitions'],
  expenses: ['/expenses'],
  reports: ['/reports', '/reports/finance', '/reports/management', '/reports/network'],
  messages: ['/messages'],
  emails: ['/emails'],
  mikrotik: ['/mikrotik', '/mikrotik/link'],
  equipment: ['/equipment'],
  settings: ['/settings', '/settings/expresswifi/edit'],
  profile: ['/profile'],
  staff_tasks: ['/staff/tasks'],
  staff_reports: ['/staff/reports'],
  staff_requisitions: ['/staff/requisitions'],
};

const DEFAULT_STAFF_PAGES = new Set(['staff_tasks', 'staff_reports', 'staff_requisitions']);

export function isTenantAdmin(tenant) {
  return Boolean(tenant?.is_admin || tenant?.is_owner || ['admin', 'tenant_admin', 'owner'].includes(String(tenant?.role || '').toLowerCase()));
}

export function pageForPath(pathname) {
  const path = pathname || '/dashboard';
  return Object.entries(PAGE_PERMISSIONS).find(([, paths]) => paths.some((prefix) => path === prefix || path.startsWith(`${prefix}/`)))?.[0] || null;
}

export function canAccessPage(tenant, pageKey) {
  if (!pageKey || pageKey === 'profile') return true;
  if (DEFAULT_STAFF_PAGES.has(pageKey) && !isTenantAdmin(tenant)) return true;
  if (isTenantAdmin(tenant)) return true;
  const permission = tenant?.permissions?.[pageKey];
  return Boolean(permission?.access && permission?.view);
}

export function canPerformAction(tenant, pageKey, action) {
  if (!pageKey || pageKey === 'profile') return true;
  if (DEFAULT_STAFF_PAGES.has(pageKey) && !isTenantAdmin(tenant)) return true;
  if (isTenantAdmin(tenant)) return true;
  const permission = tenant?.permissions?.[pageKey];
  return Boolean(permission?.access && permission?.[action]);
}

export function firstAllowedPath(tenant) {
  if (isTenantAdmin(tenant)) return '/dashboard';
  const match = Object.entries(PAGE_PERMISSIONS).find(([pageKey]) => canAccessPage(tenant, pageKey));
  return match?.[1]?.[0] || '/profile';
}
