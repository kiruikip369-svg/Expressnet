import { createContext, useContext, useMemo, useState } from 'react';
import { clearTenantTheme, storeTenantSettings } from '../utils/theme';

const AuthContext = createContext(null);

const TOKEN_KEY = 'billing_saas_token';
const TENANT_KEY = 'billing_saas_tenant';

function decodeToken(token) {
  try {
    const payload = token.split('.')[1];
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=');
    return JSON.parse(window.atob(padded));
  } catch {
    return null;
  }
}

function getStoredToken() {
  const storedToken = localStorage.getItem(TOKEN_KEY);
  const decoded = storedToken ? decodeToken(storedToken) : null;

  if (decoded?.exp && decoded.exp * 1000 < Date.now()) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TENANT_KEY);
    return null;
  }

  return storedToken;
}

function getStoredTenant() {
  try {
    return JSON.parse(localStorage.getItem(TENANT_KEY)) || null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => getStoredToken());
  const [tenant, setTenant] = useState(() => getStoredTenant());

  const login = (nextToken, nextTenant = null) => {
    const decoded = decodeToken(nextToken);
    const tenantInfo = nextTenant || (decoded ? { id: decoded.id } : null);

    localStorage.setItem(TOKEN_KEY, nextToken);
    if (tenantInfo) {
      localStorage.setItem(TENANT_KEY, JSON.stringify(tenantInfo));
      storeTenantSettings(tenantInfo);
    }

    setToken(nextToken);
    setTenant(tenantInfo);
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TENANT_KEY);
    clearTenantTheme();
    setToken(null);
    setTenant(null);
  };

  const value = useMemo(
    () => ({
      token,
      tenant,
      isAuthenticated: Boolean(token),
      login,
      logout,
    }),
    [token, tenant],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }

  return context;
}
