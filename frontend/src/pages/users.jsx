import { Check, KeyRound, Mail, Pencil, Phone, ShieldCheck, Trash2, UserPlus, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

const PERMISSION_PAGES = [
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'customers', label: 'Customers' },
  { key: 'packages', label: 'Packages' },
  { key: 'payments', label: 'Payments' },
  { key: 'vouchers', label: 'Vouchers' },
  { key: 'expenses', label: 'Expenses' },
  { key: 'reports', label: 'Reports' },
  { key: 'messages', label: 'Messages' },
  { key: 'emails', label: 'Emails' },
  { key: 'mikrotik', label: 'MikroTik' },
  { key: 'equipment', label: 'Equipment' },
  { key: 'settings', label: 'Settings' },
];

const ACTIONS = [
  { key: 'view', label: 'View' },
  { key: 'create', label: 'Create' },
  { key: 'edit', label: 'Edit' },
  { key: 'delete', label: 'Delete' },
];

const emptyUser = { name: '', role: '', email: '', phone: '', password: '' };

function buildDefaultPermissions() {
  return PERMISSION_PAGES.reduce((permissions, page) => {
    permissions[page.key] = { access: false, view: false, create: false, edit: false, delete: false };
    return permissions;
  }, {});
}

function normalizePermissions(rawPermissions = {}) {
  const permissions = buildDefaultPermissions();
  Object.keys(permissions).forEach((pageKey) => {
    if (rawPermissions[pageKey]) {
      permissions[pageKey] = { ...permissions[pageKey], ...rawPermissions[pageKey] };
    }
  });
  return permissions;
}

function normalizeMember(raw) {
  return {
    id: raw.id,
    name: raw.name || raw.full_name || raw.username || 'Unnamed user',
    email: raw.email || '',
    phone: raw.phone || '',
    role: raw.role || 'staff',
    status: raw.status || 'active',
    permissions: normalizePermissions(raw.permissions),
  };
}

function countAllowedPages(permissions) {
  return Object.values(permissions || {}).filter((permission) => permission.access).length;
}

function Checkbox({ checked, disabled, onChange, label }) {
  return (
    <label className="inline-flex items-center justify-center">
      <span className="sr-only">{label}</span>
      <input
        type="checkbox"
        className="h-4 w-4 rounded accent-[var(--dashboard-color)] disabled:cursor-not-allowed disabled:opacity-30"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
    </label>
  );
}

function Field({ label, required, children }) {
  return (
    <label className="block">
      <span className="theme-text text-xs font-semibold">
        {label}{required && <span className="text-[#ff8a00]">*</span>}
      </span>
      <div className="mt-2">{children}</div>
    </label>
  );
}

function Input(props) {
  return (
    <input
      {...props}
      className="theme-input h-10 w-full rounded-md border px-3 text-xs font-semibold outline-none transition focus:border-[var(--dashboard-color)] focus:ring-2 focus:ring-[var(--dashboard-color)]/20"
    />
  );
}

function Modal({ title, children, onClose, width = 'max-w-xl' }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <section className={`theme-card max-h-[90vh] w-full ${width} overflow-hidden rounded-lg border`}>
        <div className="theme-card-muted flex items-center justify-between border-b px-5 py-4">
          <h2 className="theme-text text-sm font-semibold">{title}</h2>
          <button type="button" className="rounded-md p-2 hover:bg-[var(--app-panel-muted)]" onClick={onClose} aria-label="Close dialog">
            <X size={17} />
          </button>
        </div>
        <div className="max-h-[calc(90vh-73px)] overflow-y-auto p-5">{children}</div>
      </section>
    </div>
  );
}

function PermissionDialog({ permissions, title, saving, onChange, onClose, onSave }) {
  const togglePageAccess = (pageKey) => {
    const page = permissions[pageKey];
    const access = !page.access;
    onChange({
      ...permissions,
      [pageKey]: access ? { ...page, access, view: true } : { access: false, view: false, create: false, edit: false, delete: false },
    });
  };

  const toggleAction = (pageKey, actionKey) => {
    const page = permissions[pageKey];
    onChange({
      ...permissions,
      [pageKey]: { ...page, access: true, view: true, [actionKey]: !page[actionKey] },
    });
  };

  return (
    <Modal title={title} onClose={onClose} width="max-w-3xl">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead>
            <tr className="theme-muted">
              <th className="w-48 pb-3 font-semibold">Page</th>
              <th className="pb-3 text-center font-semibold">Access</th>
              {ACTIONS.map((action) => (
                <th key={action.key} className="pb-3 text-center font-semibold">{action.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PERMISSION_PAGES.map((page) => {
              const permission = permissions[page.key];
              return (
                <tr key={page.key} className="border-t border-[var(--app-border)]">
                  <td className="theme-text py-3 font-semibold">{page.label}</td>
                  <td className="py-3 text-center">
                    <Checkbox label={`${page.label} access`} checked={permission.access} onChange={() => togglePageAccess(page.key)} />
                  </td>
                  {ACTIONS.map((action) => (
                    <td key={action.key} className="py-3 text-center">
                      <Checkbox
                        label={`${page.label} ${action.label}`}
                        checked={permission[action.key]}
                        disabled={!permission.access || action.key === 'view'}
                        onChange={() => toggleAction(page.key, action.key)}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-5 flex justify-end gap-3">
        <button type="button" className="theme-card h-9 rounded-md border px-4 text-xs font-semibold" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60"
          onClick={onSave}
          disabled={saving}
        >
          <ShieldCheck size={14} />
          {saving ? 'Saving...' : 'Save allowed pages'}
        </button>
      </div>
    </Modal>
  );
}

export default function Users({ embedded = false }) {
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [permissionTarget, setPermissionTarget] = useState(null);
  const [draftUser, setDraftUser] = useState(emptyUser);
  const [draftPermissions, setDraftPermissions] = useState(() => buildDefaultPermissions());
  const [creating, setCreating] = useState(false);
  const [savingPermissions, setSavingPermissions] = useState(false);
  const [removingId, setRemovingId] = useState(null);

  useEffect(() => {
    let mounted = true;
    async function loadMembers() {
      try {
        const { data } = await api.get('/team/members');
        const list = (data.results || data.members || data || []).map(normalizeMember);
        if (mounted) setMembers(list);
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load users');
      } finally {
        if (mounted) setLoading(false);
      }
    }
    loadMembers();
    return () => {
      mounted = false;
    };
  }, []);

  const totalAllowedPages = useMemo(
    () => members.reduce((total, member) => total + countAllowedPages(member.permissions), 0),
    [members]
  );

  const openCreate = () => {
    setDraftUser(emptyUser);
    setDraftPermissions(buildDefaultPermissions());
    setCreateOpen(true);
  };

  const closeCreate = () => {
    if (creating) return;
    setCreateOpen(false);
    setPermissionTarget(null);
  };

  const createUser = async (event) => {
    event.preventDefault();
    if (!draftUser.name.trim() || !draftUser.email.trim() || !draftUser.password.trim()) {
      toast.error('Name, email, and password are required');
      return;
    }
    if (draftUser.password.length < 6) {
      toast.error('Password must be at least 6 characters');
      return;
    }

    setCreating(true);
    try {
      const payload = { ...draftUser, permissions: draftPermissions };
      const { data } = await api.post('/team/invite', payload);
      const newMember = normalizeMember(data.member || data);
      setMembers((current) => [...current, newMember]);
      setCreateOpen(false);
      setPermissionTarget(null);
      setDraftUser(emptyUser);
      setDraftPermissions(buildDefaultPermissions());
      toast.success(data.message || 'User created');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to create user');
    } finally {
      setCreating(false);
    }
  };

  const openMemberPermissions = (member) => {
    setPermissionTarget({ type: 'member', memberId: member.id, permissions: normalizePermissions(member.permissions) });
  };

  const saveMemberPermissions = async () => {
    if (permissionTarget?.type !== 'member') return;
    setSavingPermissions(true);
    try {
      const { data } = await api.patch(`/team/members/${permissionTarget.memberId}/permissions`, {
        permissions: permissionTarget.permissions,
      });
      if (data?.member) {
        const updated = normalizeMember(data.member);
        setMembers((current) => current.map((member) => (member.id === updated.id ? updated : member)));
      } else {
        setMembers((current) =>
          current.map((member) =>
            member.id === permissionTarget.memberId ? { ...member, permissions: permissionTarget.permissions } : member
          )
        );
      }
      setPermissionTarget(null);
      toast.success(data?.message || 'Permissions saved');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save permissions');
    } finally {
      setSavingPermissions(false);
    }
  };

  const removeMember = async (memberId) => {
    setRemovingId(memberId);
    try {
      await api.delete(`/team/members/${memberId}`);
      setMembers((current) => current.filter((member) => member.id !== memberId));
      toast.success('User removed');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to remove user');
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className={embedded ? 'space-y-5' : 'theme-page min-h-[calc(100vh-96px)] space-y-5 rounded-lg p-4 shadow-sm'}>
      <div className={`flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between ${embedded ? '' : 'border-b border-[var(--app-border)] pb-4'}`}>
        {!embedded && (
          <div>
            <h1 className="theme-text text-xl font-semibold">Users & Permissions</h1>
            <p className="theme-muted mt-1 max-w-2xl text-xs leading-5">
              Create tenant staff accounts, choose which dashboard pages they can open, and control the actions allowed on each page.
            </p>
          </div>
        )}
        <div className="theme-card-muted grid grid-cols-2 gap-3 rounded-lg border p-3 text-xs sm:min-w-64">
          <div>
            <p className="theme-muted">Team users</p>
            <p className="theme-text mt-1 text-lg font-semibold">{members.length}</p>
          </div>
          <div>
            <p className="theme-muted">Allowed pages</p>
            <p className="theme-text mt-1 text-lg font-semibold">{totalAllowedPages}</p>
          </div>
        </div>
        <button
          type="button"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90"
          onClick={openCreate}
        >
          <UserPlus size={15} />
          Create user
        </button>
      </div>

      <section className="theme-card overflow-hidden rounded-lg border">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-xs">
            <thead className="theme-card-muted border-b">
              <tr className="theme-muted">
                <th className="px-4 py-3 font-semibold">Name</th>
                <th className="px-4 py-3 font-semibold">Role</th>
                <th className="px-4 py-3 font-semibold">Email</th>
                <th className="px-4 py-3 font-semibold">Phone</th>
                <th className="px-4 py-3 font-semibold">Allowed pages</th>
                <th className="px-4 py-3 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--app-border)]">
              {loading ? (
                <tr><td className="theme-muted px-4 py-8 text-center" colSpan="6">Loading users...</td></tr>
              ) : members.length === 0 ? (
                <tr><td className="theme-muted px-4 py-8 text-center" colSpan="6">No tenant users yet.</td></tr>
              ) : (
                members.map((member) => (
                  <tr key={member.id}>
                    <td className="theme-text px-4 py-3 font-semibold">{member.name}</td>
                    <td className="px-4 py-3 capitalize">{member.role}</td>
                    <td className="px-4 py-3"><span className="inline-flex items-center gap-1"><Mail size={12} /> {member.email || '-'}</span></td>
                    <td className="px-4 py-3"><span className="inline-flex items-center gap-1"><Phone size={12} /> {member.phone || '-'}</span></td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        className="inline-flex h-8 items-center gap-2 rounded-md border border-[var(--app-border)] px-3 text-xs font-semibold hover:bg-[var(--app-panel-muted)]"
                        onClick={() => openMemberPermissions(member)}
                      >
                        <KeyRound size={13} />
                        {countAllowedPages(member.permissions)} pages
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          className="inline-flex h-8 items-center gap-2 rounded-md border border-[var(--app-border)] px-3 text-xs font-semibold hover:bg-[var(--app-panel-muted)]"
                          onClick={() => openMemberPermissions(member)}
                        >
                          <Pencil size={13} />
                          Permissions
                        </button>
                        <button
                          type="button"
                          className="inline-flex h-8 items-center gap-2 rounded-md border border-red-900/50 px-3 text-xs font-semibold text-red-400 hover:bg-red-950/30 disabled:opacity-60"
                          onClick={() => removeMember(member.id)}
                          disabled={removingId === member.id}
                        >
                          <Trash2 size={13} />
                          {removingId === member.id ? 'Removing...' : 'Remove'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {createOpen && (
        <Modal title="Create user" onClose={closeCreate}>
          <form onSubmit={createUser}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" required>
                <Input value={draftUser.name} onChange={(event) => setDraftUser((current) => ({ ...current, name: event.target.value }))} />
              </Field>
              <Field label="Role">
                <Input
                  value={draftUser.role}
                  placeholder="e.g. Installer, Cashier, Support lead"
                  onChange={(event) => setDraftUser((current) => ({ ...current, role: event.target.value }))}
                />
              </Field>
              <Field label="Email" required>
                <Input type="email" value={draftUser.email} onChange={(event) => setDraftUser((current) => ({ ...current, email: event.target.value }))} />
              </Field>
              <Field label="Phone number">
                <Input value={draftUser.phone} onChange={(event) => setDraftUser((current) => ({ ...current, phone: event.target.value }))} />
              </Field>
              <Field label="Password" required>
                <Input
                  type="password"
                  value={draftUser.password}
                  onChange={(event) => setDraftUser((current) => ({ ...current, password: event.target.value }))}
                />
              </Field>
            </div>
            <div className="mt-4">
              <Field label="Allowed pages">
                <button
                  type="button"
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-[var(--app-border)] px-4 text-xs font-semibold hover:bg-[var(--app-panel-muted)]"
                  onClick={() => setPermissionTarget({ type: 'draft', permissions: draftPermissions })}
                >
                  <KeyRound size={14} />
                  {countAllowedPages(draftPermissions)} pages selected
                </button>
              </Field>
            </div>
            <div className="mt-5 flex justify-end gap-3">
              <button type="button" className="theme-card h-9 rounded-md border px-4 text-xs font-semibold" onClick={closeCreate}>
                Cancel
              </button>
              <button
                type="submit"
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-[var(--dashboard-color)] px-4 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60"
                disabled={creating}
              >
                <Check size={14} />
                {creating ? 'Creating...' : 'Create user'}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {permissionTarget && (
        <PermissionDialog
          title={permissionTarget.type === 'draft' ? 'Allowed pages' : 'Edit allowed pages'}
          permissions={permissionTarget.permissions}
          saving={savingPermissions}
          onChange={(permissions) => setPermissionTarget((current) => ({ ...current, permissions }))}
          onClose={() => setPermissionTarget(null)}
          onSave={() => {
            if (permissionTarget.type === 'draft') {
              setDraftPermissions(permissionTarget.permissions);
              setPermissionTarget(null);
              return;
            }
            saveMemberPermissions();
          }}
        />
      )}
    </div>
  );
}
