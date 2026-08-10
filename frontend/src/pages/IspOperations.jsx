import { Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import Modal from '../components/Modal';
import { canPerformAction } from '../utils/permissions';
import { useAuth } from '../context/AuthContext';

const blankTask = {
  title: '',
  description: '',
  customer_id: '',
  assigned_to: '',
  assigned_to_name: '',
  assigned_to_role: '',
  status: 'pending',
  priority: 'medium',
};

const statusColumns = [
  ['pending', 'Pending'],
  ['in_progress', 'In Progress'],
  ['complete', 'Complete'],
];

function priorityClass(priority) {
  if (priority === 'urgent') return 'bg-red-100 text-red-700';
  if (priority === 'high') return 'bg-orange-100 text-orange-700';
  if (priority === 'low') return 'bg-slate-100 text-slate-600';
  return 'bg-blue-100 text-blue-700';
}

export default function IspOperations() {
  const { tenant } = useAuth();
  const [tickets, setTickets] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState(blankTask);
  const [editingId, setEditingId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const canCreate = canPerformAction(tenant, 'tickets', 'create');
  const canEdit = canPerformAction(tenant, 'tickets', 'edit');
  const canDelete = canPerformAction(tenant, 'tickets', 'delete');

  async function loadTickets() {
    setLoading(true);
    try {
      const [ticketRes, teamRes] = await Promise.all([
        api.get('/tickets?all=1'),
        api.get('/staff?all=1'),
      ]);
      setTickets(Array.isArray(ticketRes.data) ? ticketRes.data : ticketRes.data.results || []);
      setTeamMembers(Array.isArray(teamRes.data) ? teamRes.data : teamRes.data.results || []);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
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

  const save = async (event) => {
    event.preventDefault();
    try {
      if (editingId) {
        await api.patch(`/tickets/${editingId}`, draft);
        toast.success('Task updated');
      } else {
        await api.post('/tickets/add', draft);
        toast.success('Task created');
      }
      setDraft(blankTask);
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

  const edit = (ticket) => {
    setDraft({
      title: ticket.title || '',
      description: ticket.description || '',
      customer_id: ticket.customer_id || '',
      assigned_to: ticket.assigned_to || '',
      assigned_to_name: ticket.assigned_to_name || '',
      assigned_to_role: ticket.assigned_to_role || '',
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
            <button type="button" className="btn-primary" onClick={() => { setDraft(blankTask); setEditingId(null); setShowForm(true); }}>
              <Plus size={16} />
              New task
            </button>
          )}
        </div>
      </section>

      {showForm && (
        <Modal title={editingId ? 'Edit Task' : 'Create Task'} onClose={() => setShowForm(false)}>
          <form className="space-y-4" onSubmit={save}>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block text-xs font-medium text-slate-500">
                Title
                <input className="form-input" value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} required />
              </label>
              <label className="block text-xs font-medium text-slate-500">
                Customer ID
                <input className="form-input" value={draft.customer_id} onChange={(event) => setDraft((current) => ({ ...current, customer_id: event.target.value }))} />
              </label>
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
          <div className="grid gap-4 p-4 xl:grid-cols-3">
            {statusColumns.map(([status, label]) => (
              <div key={status} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                <h2 className="mb-3 text-sm font-semibold text-slate-700">{label}</h2>
                <div className="space-y-3">
                  {filtered.filter((ticket) => ticket.status === status).map((ticket) => (
                    <article key={ticket.id} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <h3 className="text-sm font-semibold text-slate-900">{ticket.title}</h3>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${priorityClass(ticket.priority)}`}>{ticket.priority}</span>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs text-slate-500">{ticket.description || 'No description'}</p>
                      <p className="mt-3 text-xs text-slate-500">
                        Assigned to: <span className="font-semibold text-slate-700">{ticket.assigned_to_name || 'Unassigned'}</span>
                        {ticket.assigned_to_role ? <span> ({ticket.assigned_to_role})</span> : null}
                      </p>
                      {(canEdit || canDelete) && (
                        <div className="mt-3 flex justify-end gap-2">
                          {canEdit && <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => edit(ticket)}><Pencil size={13} />Edit</button>}
                          {canDelete && <button type="button" className="btn-secondary px-2 py-1 text-xs text-red-600" onClick={() => remove(ticket)}><Trash2 size={13} />Delete</button>}
                        </div>
                      )}
                    </article>
                  ))}
                  {filtered.filter((ticket) => ticket.status === status).length === 0 && <p className="py-8 text-center text-xs text-slate-400">No tasks</p>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
