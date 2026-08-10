import { AlertTriangle, Loader2, Package, Pencil, Plus, RotateCcw, Trash2, Wrench } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';
import { useAuth } from '../context/AuthContext';
import { canPerformAction } from '../utils/permissions';

// Equipment (routers, switches, access points...) is issued and tracked by
// who has it — it isn't expected to come back on a schedule, so it carries
// no return/lost status. Tools are checked out for a task and are expected
// back, so they carry a status the staff member (or whoever logs it) updates.
const ITEM_TYPES = [
  { key: 'equipment', label: 'Equipment', hint: 'Routers, switches, access points, CPE...' },
  { key: 'tool', label: 'Tool', hint: 'Crimpers, testers, ladders, drills...' },
];

const TOOL_STATUSES = [
  { key: 'with_staff', label: 'With staff' },
  { key: 'returned', label: 'Returned' },
  { key: 'lost', label: 'Lost' },
];

const blankDraft = { itemType: 'equipment', itemName: '', quantity: '1', staffId: '', task: '', status: 'with_staff' };

function StatusPill({ status }) {
  const styles = {
    with_staff: 'bg-amber-50 text-amber-700 border-amber-200',
    returned: 'bg-green-50 text-green-700 border-green-200',
    lost: 'bg-red-50 text-red-700 border-red-200',
  };
  const labels = { with_staff: 'With staff', returned: 'Returned', lost: 'Lost' };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${styles[status] || styles.with_staff}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {labels[status] || status}
    </span>
  );
}

function formatDate(value) {
  if (!value) return '';
  try {
    return new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return '';
  }
}

function staffLabel(staff) {
  const name = staff.name || staff.email || 'Unnamed staff';
  const role = staff.role ? ` - ${staff.role}` : '';
  return `${name}${role}`;
}

function extractStaff(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.results)) return data.results;
  if (data && typeof data === 'object') {
    return Object.entries(data).map(([id, staff]) => ({ id, ...(staff || {}) }));
  }
  return [];
}

export default function Equipment() {
  const { tenant } = useAuth();
  const [staffList, setStaffList] = useState([]);
  const [staffLoading, setStaffLoading] = useState(true);

  const [assignments, setAssignments] = useState([]);
  const [draft, setDraft] = useState(blankDraft);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [typeFilter, setTypeFilter] = useState('all');
  const [showForm, setShowForm] = useState(false);
  const canCreate = canPerformAction(tenant, 'equipment', 'create');
  const canEdit = canPerformAction(tenant, 'equipment', 'edit');
  const canDelete = canPerformAction(tenant, 'equipment', 'delete');

  useEffect(() => {
    let mounted = true;
    async function loadStaff() {
      try {
        const { data } = await api.get('/staff?all=1');
        if (mounted) setStaffList(extractStaff(data));
      } catch (error) {
        toast.error(error.response?.data?.message || 'Failed to load staff list');
      } finally {
        if (mounted) setStaffLoading(false);
      }
    }
    loadStaff();
    return () => {
      mounted = false;
    };
  }, []);

  const updateDraft = (field, value) => {
    setDraft((current) => {
      const next = { ...current, [field]: value };
      // Switching to equipment makes status meaningless; reset it so a
      // half-filled tool status can't leak into an equipment record.
      if (field === 'itemType' && value === 'equipment') {
        next.status = 'with_staff';
      }
      return next;
    });
  };

  const resetForm = () => {
    setDraft(blankDraft);
    setEditingId(null);
  };

  const openAddForm = () => {
    resetForm();
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    resetForm();
  };

  const save = (event) => {
    event.preventDefault();
    if (!draft.itemName.trim()) {
      toast.error('Enter the equipment or tool name');
      return;
    }
    const quantity = draft.itemType === 'equipment' ? Math.max(Number.parseInt(draft.quantity, 10) || 1, 1) : 1;
    if (!draft.staffId) {
      toast.error('Select which staff member is receiving it');
      return;
    }
    const staff = staffList.find((item) => item.id === draft.staffId);
    setSaving(true);
    setAssignments((current) => {
      if (editingId) {
        return current.map((item) =>
          item.id === editingId
            ? {
                ...item,
                itemType: draft.itemType,
                itemName: draft.itemName.trim(),
                quantity,
                staffId: draft.staffId,
                staffName: staff?.name || item.staffName,
                task: draft.task.trim(),
                status: draft.itemType === 'tool' ? draft.status : null,
              }
            : item
        );
      }
      const record = {
        id: `EQ-${Date.now().toString().slice(-6)}`,
        itemType: draft.itemType,
        itemName: draft.itemName.trim(),
        quantity,
        staffId: draft.staffId,
        staffName: staff?.name || 'Unknown staff',
        task: draft.task.trim(),
        status: draft.itemType === 'tool' ? draft.status : null,
        issuedAt: new Date().toISOString(),
      };
      return [record, ...current];
    });
    toast.success(editingId ? 'Assignment updated' : 'Assignment recorded');
    setSaving(false);
    setShowForm(false);
    resetForm();
  };

  const startEdit = (record) => {
    setDraft({
      itemType: record.itemType,
      itemName: record.itemName,
      quantity: String(record.quantity || 1),
      staffId: record.staffId,
      task: record.task || '',
      status: record.status || 'with_staff',
    });
    setEditingId(record.id);
    setShowForm(true);
  };

  const setToolStatus = (record, status) => {
    setAssignments((current) => current.map((item) => (item.id === record.id ? { ...item, status } : item)));
    toast.success(status === 'returned' ? 'Marked as returned' : 'Marked as lost');
  };

  const removeRecord = (record) => {
    setAssignments((current) => current.filter((item) => item.id !== record.id));
    if (editingId === record.id) resetForm();
    toast.success('Assignment record deleted');
  };

  const visibleAssignments = useMemo(
    () => (typeFilter === 'all' ? assignments : assignments.filter((item) => item.itemType === typeFilter)),
    [assignments, typeFilter]
  );

  const withStaffCount = assignments.filter((item) => item.itemType === 'tool' && item.status === 'with_staff').length;
  const lostCount = assignments.filter((item) => item.status === 'lost').length;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="page-title">Equipment &amp; Tools</h1>
          <p className="page-subtitle">Record equipment and tools handed to staff for a task, and track what still needs to come back.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="btn-secondary"><Wrench size={15} />{withStaffCount} tools out</span>
          <span className="btn-secondary"><AlertTriangle size={15} />{lostCount} lost</span>
          {canCreate && (
            <button className="btn-primary" type="button" onClick={openAddForm}>
              <Plus size={15} />Record assignment
            </button>
          )}
        </div>
      </div>

      <div className="surface-card flex items-center gap-2 p-2">
        {[
          { key: 'all', label: 'All' },
          { key: 'equipment', label: 'Equipment' },
          { key: 'tool', label: 'Tools' },
        ].map((filter) => (
          <button
            key={filter.key}
            type="button"
            onClick={() => setTypeFilter(filter.key)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              typeFilter === filter.key ? 'bg-app-accent text-white' : 'text-slate-500 hover:bg-slate-100'
            }`}
          >
            {filter.label}
          </button>
        ))}
      </div>

      <div className="surface-card overflow-x-auto">
        {visibleAssignments.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-slate-400">
            {assignments.length === 0 ? 'No equipment or tools have been recorded yet.' : 'Nothing matches this filter.'}
          </p>
        ) : (
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs font-medium text-slate-500">
                <th className="px-4 py-3">Item</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Qty</th>
                <th className="px-4 py-3">Staff</th>
                <th className="px-4 py-3">Task</th>
                <th className="px-4 py-3">Issued</th>
                <th className="px-4 py-3">Status</th>
                  {(canEdit || canDelete) && <th className="px-4 py-3 text-right">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {visibleAssignments.map((record) => (
                <tr key={record.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {record.itemType === 'equipment' ? <Package size={14} className="text-slate-400" /> : <Wrench size={14} className="text-slate-400" />}
                      <span className="font-medium text-slate-950">{record.itemName}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-600 capitalize">{record.itemType}</td>
                  <td className="px-4 py-3 text-slate-600">{record.itemType === 'equipment' ? record.quantity || 1 : '-'}</td>
                  <td className="px-4 py-3 text-slate-600">{record.staffName}</td>
                  <td className="px-4 py-3 text-slate-500">{record.task || '—'}</td>
                  <td className="px-4 py-3 text-slate-500">{formatDate(record.issuedAt)}</td>
                  <td className="px-4 py-3">
                    {record.itemType === 'tool' ? <StatusPill status={record.status} /> : <span className="text-xs text-slate-400">Not tracked</span>}
                  </td>
                  {(canEdit || canDelete) && (
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        {canEdit && record.itemType === 'tool' && record.status === 'with_staff' && (
                          <>
                            <button className="btn-secondary" type="button" onClick={() => setToolStatus(record, 'returned')}>
                              <RotateCcw size={14} />Returned
                            </button>
                            <button className="btn-danger" type="button" onClick={() => setToolStatus(record, 'lost')}>
                              <AlertTriangle size={14} />Lost
                            </button>
                          </>
                        )}
                        {canEdit && (
                          <button className="btn-secondary" type="button" onClick={() => startEdit(record)}>
                            <Pencil size={14} />Edit
                          </button>
                        )}
                        {canDelete && (
                          <button className="btn-danger" type="button" onClick={() => removeRecord(record)}>
                            <Trash2 size={14} />Delete
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {staffLoading && (
        <p className="flex items-center gap-2 text-xs text-slate-400">
          <Loader2 size={13} className="animate-spin" />
          Loading staff list...
        </p>
      )}

      {showForm && (
        <Modal title={editingId ? 'Edit assignment' : 'Record new assignment'} onClose={closeForm}>
          <form className="space-y-3" onSubmit={save}>
            <div>
              <p className="mb-1.5 text-xs font-medium text-slate-600">What is being given out?</p>
              <div className="grid grid-cols-2 gap-2">
                {ITEM_TYPES.map((type) => (
                  <button
                    key={type.key}
                    type="button"
                    onClick={() => updateDraft('itemType', type.key)}
                    className={`rounded-md border px-3 py-2 text-left text-xs font-medium transition ${
                      draft.itemType === type.key ? 'border-app-accent bg-app-accent/5 text-app-accent' : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                    }`}
                  >
                    <span className="flex items-center gap-1.5">
                      {type.key === 'equipment' ? <Package size={14} /> : <Wrench size={14} />}
                      {type.label}
                    </span>
                    <span className="mt-0.5 block text-[11px] font-normal text-slate-400">{type.hint}</span>
                  </button>
                ))}
              </div>
            </div>

            <input
              className="form-input"
              placeholder={draft.itemType === 'equipment' ? 'Equipment name (e.g. RB4011 Router #3)' : 'Tool name (e.g. Cable crimper)'}
              value={draft.itemName}
              onChange={(event) => updateDraft('itemName', event.target.value)}
            />

            {draft.itemType === 'equipment' && (
              <input
                className="form-input"
                type="number"
                min="1"
                step="1"
                placeholder="Quantity"
                value={draft.quantity}
                onChange={(event) => updateDraft('quantity', event.target.value)}
              />
            )}

            <select
              className="form-input"
              value={draft.staffId}
              onChange={(event) => updateDraft('staffId', event.target.value)}
              disabled={staffLoading}
            >
              <option value="">{staffLoading ? 'Loading staff...' : 'Select staff member'}</option>
              {staffList.map((staff) => (
                <option key={staff.id} value={staff.id}>
                  {staffLabel(staff)}
                </option>
              ))}
            </select>
            {!staffLoading && staffList.length === 0 && (
              <p className="text-xs text-amber-600">No staff accounts yet — add one under Settings &rarr; Users first.</p>
            )}

            <input
              className="form-input"
              placeholder="Task / reason (optional)"
              value={draft.task}
              onChange={(event) => updateDraft('task', event.target.value)}
            />

            {draft.itemType === 'tool' ? (
              <div>
                <p className="mb-1.5 text-xs font-medium text-slate-600">Status</p>
                <select className="form-input" value={draft.status} onChange={(event) => updateDraft('status', event.target.value)}>
                  {TOOL_STATUSES.map((status) => (
                    <option key={status.key} value={status.key}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
               
              </p>
            )}

            <div className="flex gap-2 pt-1">
              <button className="btn-primary flex-1" type="submit" disabled={saving}>
                <Plus size={15} />
                {editingId ? 'Save changes' : 'Record assignment'}
              </button>
              <button className="btn-secondary" type="button" onClick={closeForm}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
