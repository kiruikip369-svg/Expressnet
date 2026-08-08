export const PAGE_PERMISSIONS = {
  dashboard: ['/dashboard'],
  customers: ['/customers', '/pppoe-customers', '/active-users'],
  tickets: ['/tickets'],
  packages: ['/packages'],
  payments: ['/payments'],
  vouchers: ['/vouchers'],
  expenses: ['/expenses'],
  reports: ['/reports'],
  messages: ['/messages'],
  emails: ['/emails'],
  mikrotik: ['/mikrotik', '/mikrotik/link'],
  equipment: ['/equipment'],
  settings: ['/settings', '/settings/expresswifi/edit'],
  profile: ['/profile'],
};

export function isTenantAdmin(tenant) {
  return Boolean(tenant?.is_admin || tenant?.is_owner || ['admin', 'tenant_admin', 'owner'].includes(String(tenant?.role || '').toLowerCase()));
}

export function pageForPath(pathname) {
  const path = pathname || '/dashboard';
  return Object.entries(PAGE_PERMISSIONS).find(([, paths]) => paths.some((prefix) => path === prefix || path.startsWith(`${prefix}/`)))?.[0] || null;
}

export function canAccessPage(tenant, pageKey) {
  if (!pageKey || pageKey === 'profile') return true;
  if (isTenantAdmin(tenant)) return true;
  const permission = tenant?.permissions?.[pageKey];
  return Boolean(permission?.access && permission?.view);
}

export function firstAllowedPath(tenant) {
  if (isTenantAdmin(tenant)) return '/dashboard';
  const match = Object.entries(PAGE_PERMISSIONS).find(([pageKey]) => canAccessPage(tenant, pageKey));
  return match?.[1]?.[0] || '/profile';
}
