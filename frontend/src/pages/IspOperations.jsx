import { Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';
import { canPerformAction } from '../utils/permissions';
import { useAuth } from '../context/AuthContext';

const TASK_TYPES = ['Installation', 'Maintainance', 'Implementation', 'Marketing'];
const OTHER_TASK_TYPE = 'Other';

// Task types that do NOT need a customer id
const NO_CUSTOMER_TASK_TYPES = ['Implementation', 'Marketing'];

const blankTask = {
  title: TASK_TYPES[0],
  description: '',
  customer_id: '',
  assigned_to: '',
  assigned_to_name: '',
  assigned_to_role: '',
  mikrotik_id: '',
  mikrotik_name: '',
  status: 'pending',
  priority: 'medium',
};

const statusColumns = [
  ['pending', 'Pending'],
  ['in_progress', 'In Progress'],
  ['complete', 'Complete'],
  ['bounced', 'Bounced'],
];

function priorityClass(priority) {
  if (priority === 'urgent') return 'bg-red-100 text-red-700';
  if (priority === 'high') return 'bg-orange-100 text-orange-700';
  if (priority === 'low') return 'bg-slate-100 text-slate-600';
  return 'bg-blue-100 text-blue-700';
}

function responseItems(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

function routerItems(data) {
  const linkedRouters = data?.linked_routers || {};
  return Object.entries(linkedRouters).map(([id, router]) => ({
    id: router?.id || id,
    name: router?.name || router?.identity || router?.board_name || router?.last_seen_ip || id,
  }));
}

export default function IspOperations() {
  const { tenant } = useAuth();
  const [tickets, setTickets] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [mikrotiks, setMikrotiks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState(blankTask);
  const [editingId, setEditingId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  // Controls the task-type <select>: one of TASK_TYPES or OTHER_TASK_TYPE
  const [taskTypeChoice, setTaskTypeChoice] = useState(TASK_TYPES[0]);
  const canCreate = canPerformAction(tenant, 'tickets', 'create');
  const canEdit = canPerformAction(tenant, 'tickets', 'edit');
  const canDelete = canPerformAction(tenant, 'tickets', 'delete');

  const isMaintenance = taskTypeChoice === 'Maintainance';
  const hidesCustomer = NO_CUSTOMER_TASK_TYPES.includes(taskTypeChoice);

  async function loadTickets() {
    setLoading(true);
    const [ticketResult, teamResult, mikrotikResult] = await Promise.allSettled([
      api.get('/tickets?all=1'),
      api.get('/staff?all=1'),
      api.get('/settings/mikrotik'),
    ]);

    if (ticketResult.status === 'fulfilled') {
      setTickets(responseItems(ticketResult.value.data));
    } else {
      toast.error(ticketResult.reason?.response?.data?.message || 'Failed to load tasks');
    }

    if (teamResult.status === 'fulfilled') {
      setTeamMembers(responseItems(teamResult.value.data));
    } else {
      setTeamMembers([]);
      toast.error(teamResult.reason?.response?.data?.message || 'Failed to load staff members');
    }

    if (mikrotikResult.status === 'fulfilled') {
      setMikrotiks(routerItems(mikrotikResult.value.data));
    } else {
      setMikrotiks([]);
    }

    setLoading(false);
  }

  useEffect(() => {
    loadTickets();
  }, []);

  const assignableMembers = useMemo(() => {
    return teamMembers.filter((member) => String(member.status || 'active').toLowerCase() === 'active');
  }, [teamMembers]);

  const filtered = useMemo(() => {
    return tickets.filter((ticket) => `${ticket.title} ${ticket.description} ${ticket.priority} ${ticket.status} ${ticket.assigned_to_name || ''} ${ticket.assigned_to_role || ''}`.toLowerCase().includes(query.toLowerCase()));
  }, [tickets, query]);

  // Keep dependent fields in sync whenever the task type changes
  const handleTaskTypeChange = (value) => {
    setTaskTypeChoice(value);

    setDraft((current) => {
      const next = { ...current };

      if (value === OTHER_TASK_TYPE) {
        // Let the user type their own task type; clear any predefined value
        next.title = current.title && !TASK_TYPES.includes(current.title) ? current.title : '';
      } else {
        next.title = value;
      }

      // Only "Maintainance" needs a Mikrotik selection
      if (value !== 'Maintainance') {
        next.mikrotik_id = '';
        next.mikrotik_name = '';
      }

      // Implementation/Marketing tasks don't need a customer id
      if (NO_CUSTOMER_TASK_TYPES.includes(value)) {
        next.customer_id = '';
      }

      return next;
    });
  };

  const save = async (event) => {
    event.preventDefault();
    if (!draft.title.trim()) {
      toast.error('Please provide a task type');
      return;
    }
    try {
      if (editingId) {
        await api.patch(`/tickets/${editingId}`, draft);
        toast.success('Task updated');
      } else {
        await api.post('/tickets/add', draft);
        toast.success('Task created');
      }
      setDraft({ ...blankTask });
      setTaskTypeChoice(TASK_TYPES[0]);
      setEditingId(null);
      setShowForm(false);
      loadTickets();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to save task');
    }
  };

  const updateAssignee = (memberId) => {
    const member = teamMembers.find((item) => item.id === memberId);
    setDraft((current) => ({
      ...current,
      assigned_to: member?.id || '',
      assigned_to_name: member?.name || '',
      assigned_to_role: member?.role || '',
    }));
  };

  const updateMikrotik = (mikrotikId) => {
    const mikrotik = mikrotiks.find((item) => item.id === mikrotikId);
    setDraft((current) => ({
      ...current,
      mikrotik_id: mikrotik?.id || '',
      mikrotik_name: mikrotik?.name || '',
    }));
  };

  const openCreateForm = () => {
    setDraft({ ...blankTask, title: TASK_TYPES[0] });
    setTaskTypeChoice(TASK_TYPES[0]);
    setEditingId(null);
    setShowForm(true);
  };

  const edit = (ticket) => {
    const title = ticket.title || '';
    const choice = TASK_TYPES.includes(title) ? title : (title ? OTHER_TASK_TYPE : TASK_TYPES[0]);
    setTaskTypeChoice(choice);
    setDraft({
      title,
      description: ticket.description || '',
      customer_id: ticket.customer_id || '',
      assigned_to: ticket.assigned_to || '',
      assigned_to_name: ticket.assigned_to_name || '',
      assigned_to_role: ticket.assigned_to_role || '',
      mikrotik_id: ticket.mikrotik_id || '',
      mikrotik_name: ticket.mikrotik_name || '',
      status: ticket.status || 'pending',
      priority: ticket.priority || 'medium',
    });
    setEditingId(ticket.id);
    setShowForm(true);
  };

  const remove = async (ticket) => {
    if (!window.confirm(`Delete task "${ticket.title}"?`)) return;
    try {
      await api.delete(`/tickets/${ticket.id}`);
      toast.success('Task deleted');
      loadTickets();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to delete task');
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">Tasks</h1>
            <p className="text-sm text-slate-500">Assign field work, follow-ups, customer support, and marketing tasks.</p>
          </div>
          {canCreate && (
            <button type="button" className="btn-primary" onClick={openCreateForm}>
              <Plus size={16} />
              New task
            </button>
          )}
        </div>
      </section>

      {showForm && (
        <Modal title={editingId ? 'Edit Task' : 'Create Task'} onClose={() => setShowForm(false)}>
          <form className="space-y-4" onSubmit={save}>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-xs font-medium text-slate-500">
                Task type
                <select
                  className="form-input"
                  value={taskTypeChoice}
                  onChange={(event) => handleTaskTypeChange(event.target.value)}
                >
                  {TASK_TYPES.map((type) => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                  <option value={OTHER_TASK_TYPE}>Other (specify)</option>
                </select>
              </label>

              {taskTypeChoice === OTHER_TASK_TYPE && (
                <label className="block text-xs font-medium text-slate-500">
                  Specify task type
                  <input
                    className="form-input"
                    value={draft.title}
                    onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                    placeholder="e.g. Site survey"
                    required
                  />
                </label>
              )}

              {!hidesCustomer && (
                <label className="block text-xs font-medium text-slate-500">
                  Customer ID <span className="font-normal text-slate-400">(optional)</span>
                  <input
                    className="form-input"
                    value={draft.customer_id}
                    onChange={(event) => setDraft((current) => ({ ...current, customer_id: event.target.value }))}
                  />
                </label>
              )}

              {isMaintenance && (
                <label className="block text-xs font-medium text-slate-500">
                  Mikrotik
                  <select
                    className="form-input"
                    value={draft.mikrotik_id}
                    onChange={(event) => updateMikrotik(event.target.value)}
                  >
                    <option value="">Select mikrotik</option>
                    {mikrotiks.map((mikrotik) => (
                      <option key={mikrotik.id} value={mikrotik.id}>{mikrotik.name}</option>
                    ))}
                  </select>
                </label>
              )}

              <label className="block text-xs font-medium text-slate-500">
                Assign to
                <select className="form-input" value={draft.assigned_to} onChange={(event) => updateAssignee(event.target.value)}>
                  <option value="">Select staff member</option>
                  {assignableMembers.map((member) => (
                    <option key={member.id} value={member.id}>{member.name} - {member.role}</option>
                  ))}
                </select>
              </label>

              <label className="block text-xs font-medium text-slate-500">
                Status
                <select className="form-input" value={draft.status} onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}>
                  {statusColumns.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>

              <label className="block text-xs font-medium text-slate-500">
                Priority
                <select className="form-input" value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))}>
                  {['low', 'medium', 'high', 'urgent'].map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            </div>

            <label className="block text-xs font-medium text-slate-500">
              Description
              <textarea className="form-input min-h-28" value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} />
            </label>

            <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
              <button type="button" className="btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button type="submit" className="btn-primary">{editingId ? 'Update task' : 'Create task'}</button>
            </div>
          </form>
        </Modal>
      )}

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 p-4">
          <label className="relative block max-w-sm">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input className="form-input mt-0 pl-9" placeholder="Search tasks" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
        </div>
        {loading ? (
          <div className="py-16 text-center text-sm text-slate-400">Loading tasks...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[980px] w-full divide-y divide-slate-200">
              <thead className="table-head">
                <tr>
                  <th className="px-4 py-3">Task</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">Customer</th>
                  <th className="px-4 py-3">Mikrotik</th>
                  <th className="px-4 py-3">Assigned to</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Priority</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.length === 0 ? (
                  <tr><td className="table-cell text-slate-500" colSpan="8">No tasks found.</td></tr>
                ) : filtered.map((ticket) => (
                  <tr key={ticket.id} className="bg-white">
                    <td className="table-cell font-semibold text-slate-950">{ticket.title}</td>
                    <td className="table-cell max-w-xs truncate text-slate-500">{ticket.description || 'No description'}</td>
                    <td className="table-cell">{ticket.customer_id || '-'}</td>
                    <td className="table-cell">{ticket.mikrotik_name || '-'}</td>
                    <td className="table-cell">
                      <span className="font-semibold text-slate-700">{ticket.assigned_to_name || 'Unassigned'}</span>
                      {ticket.assigned_to_role ? <span className="text-slate-500"> ({ticket.assigned_to_role})</span> : null}
                    </td>
                    <td className="table-cell">
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold capitalize text-slate-700">
                        {statusColumns.find(([value]) => value === ticket.status)?.[1] || ticket.status || 'Pending'}
                      </span>
                    </td>
                    <td className="table-cell">
                      <span className={`rounded-full px-2 py-1 text-xs font-semibold ${priorityClass(ticket.priority)}`}>{ticket.priority}</span>
                    </td>
                    <td className="table-cell">
                      {(canEdit || canDelete) && (
                        <div className="flex justify-end gap-2">
                          {canEdit && <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => edit(ticket)}><Pencil size={13} />Edit</button>}
                          {canDelete && <button type="button" className="btn-secondary px-2 py-1 text-xs text-red-600" onClick={() => remove(ticket)}><Trash2 size={13} />Delete</button>}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
