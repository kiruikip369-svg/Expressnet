export const THEME_STORAGE_KEY = 'tenant_settings';
export const TENANT_THEME_EVENT = 'tenant-theme-change';

export const DEFAULT_TENANT_THEME = {
  themeColor: '#fa8200',
  themeMode: 'light',
  darkMode: false,
  font: 'Roboto',
};

function canUseDom() {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

export function normalizeThemeColor(value, fallback = DEFAULT_TENANT_THEME.themeColor) {
  const raw = String(value || '').trim();
  if (/^#[0-9a-f]{6}$/i.test(raw)) return raw.toLowerCase();
  if (/^[0-9a-f]{6}$/i.test(raw)) return `#${raw.toLowerCase()}`;
  return fallback;
}

function mixWithWhite(hex, amount = 0.72) {
  const color = normalizeThemeColor(hex).slice(1);
  const parts = [0, 2, 4].map((start) => parseInt(color.slice(start, start + 2), 16));
  const mixed = parts.map((part) => Math.round(part + (255 - part) * amount));
  return `#${mixed.map((part) => part.toString(16).padStart(2, '0')).join('')}`;
}

function mixWithBlack(hex, amount = 0.18) {
  const color = normalizeThemeColor(hex).slice(1);
  const parts = [0, 2, 4].map((start) => parseInt(color.slice(start, start + 2), 16));
  const mixed = parts.map((part) => Math.round(part * (1 - amount)));
  return `#${mixed.map((part) => part.toString(16).padStart(2, '0')).join('')}`;
}

function hexToRgb(hex) {
  const color = normalizeThemeColor(hex).slice(1);
  return [0, 2, 4].map((start) => parseInt(color.slice(start, start + 2), 16));
}

function contrastColor(hex) {
  const [r, g, b] = hexToRgb(hex).map((value) => {
    const channel = value / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.58 ? '#111827' : '#ffffff';
}

function normalizeMode(value, darkMode = false) {
  const mode = String(value || '').trim().toLowerCase();
  if (['light', 'dark', 'system'].includes(mode)) return mode;
  return darkMode ? 'dark' : 'light';
}

export function resolveThemeMode(mode) {
  if (mode === 'system' && canUseDom()) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  return mode === 'dark' ? 'dark' : 'light';
}

export function normalizeTenantTheme(settings = {}) {
  const themeColor = normalizeThemeColor(settings.themeColor || settings.theme_color);
  const themeMode = normalizeMode(settings.themeMode || settings.theme_mode, settings.darkMode ?? settings.dark_mode);
  return {
    ...settings,
    themeColor,
    theme_color: themeColor,
    themeMode,
    theme_mode: themeMode,
    darkMode: themeMode === 'dark',
    dark_mode: themeMode === 'dark',
    font: settings.font || DEFAULT_TENANT_THEME.font,
  };
}

export function getStoredTenantSettings() {
  if (!canUseDom()) return DEFAULT_TENANT_THEME;
  try {
    return {
      ...DEFAULT_TENANT_THEME,
      ...(JSON.parse(window.localStorage.getItem(THEME_STORAGE_KEY) || '{}') || {}),
    };
  } catch {
    return DEFAULT_TENANT_THEME;
  }
}

export function applyTenantTheme(settings = getStoredTenantSettings()) {
  if (!canUseDom()) return normalizeTenantTheme(settings);

  const theme = normalizeTenantTheme(settings);
  const root = document.documentElement;
  const accentSoft = mixWithWhite(theme.themeColor);

  root.style.setProperty('--dashboard-color', theme.themeColor);
  root.style.setProperty('--dashboard-color-soft', accentSoft);
  root.style.setProperty('--app-accent', theme.themeColor);
  root.style.setProperty('--app-accent-soft', accentSoft);
  root.style.setProperty('--app-accent-strong', mixWithBlack(theme.themeColor));
  root.style.setProperty('--app-accent-muted', mixWithWhite(theme.themeColor, 0.9));
  root.style.setProperty('--app-accent-contrast', contrastColor(theme.themeColor));
  root.style.setProperty('--app-focus-ring', mixWithWhite(theme.themeColor, 0.82));
  root.style.setProperty('--app-font-family', `${theme.font}, Roboto, ui-sans-serif, system-ui, sans-serif`);
  root.dataset.theme = resolveThemeMode(theme.themeMode);
  root.dataset.themeMode = theme.themeMode;

  return theme;
}

export function storeTenantSettings(settings = {}) {
  if (!canUseDom()) return applyTenantTheme(settings);

  const merged = {
    ...getStoredTenantSettings(),
    ...settings,
  };
  const theme = normalizeTenantTheme(merged);
  window.localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(theme));
  applyTenantTheme(theme);
  window.dispatchEvent(new CustomEvent(TENANT_THEME_EVENT, { detail: theme }));
  return theme;
}

export function clearTenantTheme() {
  if (!canUseDom()) return;
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  applyTenantTheme(DEFAULT_TENANT_THEME);
  window.dispatchEvent(new CustomEvent(TENANT_THEME_EVENT, { detail: DEFAULT_TENANT_THEME }));
}
