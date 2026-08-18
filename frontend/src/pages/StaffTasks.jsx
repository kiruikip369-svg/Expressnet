import {
  AlertCircle,
  Briefcase,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  FileText,
  Mail,
  MapPin,
  Phone,
  User,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

function initials(name = '') {
  return (
    name
      .split(' ')
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || 'U'
  );
}

function priorityClasses(priority) {
  switch ((priority || '').toLowerCase()) {
    case 'high':
      return 'bg-red-50 text-red-600';
    case 'medium':
      return 'bg-amber-50 text-amber-600';
    case 'low':
      return 'bg-emerald-50 text-emerald-600';
    default:
      return 'bg-slate-100 text-slate-600';
  }
}

function statusClasses(status) {
  switch ((status || '').toLowerCase()) {
    case 'complete':
    case 'completed':
      return 'bg-emerald-50 text-emerald-600';
    case 'in_progress':
      return 'bg-blue-50 text-blue-600';
    case 'bounced':
      return 'bg-red-50 text-red-600';
    default:
      return 'bg-amber-50 text-amber-600';
  }
}

const isDone = (status) => ['complete', 'completed'].includes((status || '').toLowerCase());

function dueMeta(dueDate) {
  if (!dueDate) return { label: '—', overdue: false, dueToday: false };
  const due = new Date(dueDate);
  const now = new Date();
  due.setHours(0, 0, 0, 0);
  now.setHours(0, 0, 0, 0);
  const diffDays = Math.round((due - now) / 86400000);
  if (diffDays < 0) {
    return { label: `${Math.abs(diffDays)} day${Math.abs(diffDays) === 1 ? '' : 's'} overdue`, overdue: true, dueToday: false };
  }
  if (diffDays === 0) return { label: 'Today', overdue: false, dueToday: true };
  return { label: `${diffDays} day${diffDays === 1 ? '' : 's'} left`, overdue: false, dueToday: false };
}

export default function StaffTasks() {
  const [tasks, setTasks] = useState([]);
  const [profile, setProfile] = useState(null);
  const [reports, setReports] = useState({});
  const [bounceReasons, setBounceReasons] = useState({});
  const [busyId, setBusyId] = useState('');
  const [expandedId, setExpandedId] = useState('');

  const load = async () => {
    try {
      const { data } = await api.get('/staff/tasks?all=1');
      setTasks(items(data));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load assigned tasks');
    }
  };

  // Adjust the endpoint/shape to whatever your staff-profile API actually returns.
  const loadProfile = async () => {
    try {
      const { data } = await api.get('/staff/profile');
      setProfile(data?.staff || data);
    } catch {
      // Profile card just falls back to placeholders if this endpoint isn't wired up yet.
    }
  };

  useEffect(() => {
    load();
    loadProfile();
  }, []);

  const stats = useMemo(() => {
    const completed = tasks.filter((t) => isDone(t.status)).length;
    const pending = tasks.filter((t) => !isDone(t.status)).length;
    const overdue = tasks.filter((t) => !isDone(t.status) && dueMeta(t.due_date).overdue).length;
    const rate = tasks.length ? Math.round((completed / tasks.length) * 100) : 0;
    return { completed, pending, overdue, rate };
  }, [tasks]);

  const pendingTasks = useMemo(() => tasks.filter((t) => !isDone(t.status)), [tasks]);

  const updateTask = async (task, payload, message) => {
    setBusyId(task.id);
    try {
      const { data } = await api.patch(`/staff/tasks/${task.id}`, payload);
      setTasks((current) => current.map((item) => (item.id === task.id ? data.task : item)));
      toast.success(message);
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to update task');
    } finally {
      setBusyId('');
    }
  };

  const submitReport = async (task) => {
    const report = (reports[task.id] || '').trim();
    if (!report) return toast.error('Write a work report first');
    setBusyId(task.id);
    try {
      await api.post('/staff/reports', { task_id: task.id, task_title: task.title, report });
      const { data } = await api.patch(`/staff/tasks/${task.id}`, { work_report: report, status: 'complete' });
      setTasks((current) => current.map((item) => (item.id === task.id ? data.task : item)));
      setReports((current) => ({ ...current, [task.id]: '' }));
      toast.success('Work report submitted');
      setExpandedId('');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit report');
    } finally {
      setBusyId('');
    }
  };

  const bounceTask = async (task) => {
    const reason = (bounceReasons[task.id] || '').trim();
    if (!reason) return toast.error('Add a bounce reason first');
    await updateTask(task, { bounce_reason: reason, status: 'bounced' }, 'Task marked bounced');
    setBounceReasons((current) => ({ ...current, [task.id]: '' }));
  };

  return (
    <div className="space-y-4">
      {/* Profile + stats row */}
      <div className="grid gap-4 xl:grid-cols-3">
        <section className="surface-card flex flex-wrap items-start gap-5 p-5 xl:col-span-2">
          <div className="relative shrink-0">
            <div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-full bg-blue-100 text-lg font-semibold text-blue-700">
              {profile?.avatar_url ? (
                <img src={profile.avatar_url} alt={profile?.full_name || 'Staff'} className="h-full w-full object-cover" />
              ) : (
                initials(profile?.full_name)
              )}
            </div>
            <span className="absolute bottom-0 right-0 h-3.5 w-3.5 rounded-full bg-emerald-500 ring-2 ring-white" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="theme-text text-lg font-semibold">{profile?.full_name || 'Staff Member'}</h1>
              <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-600">
                Staff ID: {profile?.staff_id || '—'}
              </span>
            </div>
            <p className="text-sm text-blue-600">{profile?.role || 'Field Staff'}</p>

            <div className="mt-3 grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
              <span className="flex items-center gap-1.5">
                <Mail size={13} /> {profile?.email || '—'}
              </span>
              <span className="flex items-center gap-1.5">
                <Phone size={13} /> {profile?.phone || '—'}
              </span>
              <span className="flex items-center gap-1.5">
                <MapPin size={13} /> {profile?.location || '—'}
              </span>
              <span className="flex items-center gap-1.5">
                <Briefcase size={13} /> {profile?.department || '—'}
              </span>
              <span className="flex items-center gap-1.5">
                <Calendar size={13} /> Joined {profile?.date_joined || '—'}
              </span>
              <span className="flex items-center gap-1.5">
                <User size={13} /> Reporting to {profile?.manager_name || '—'}
              </span>
            </div>
          </div>
        </section>

        <section className="surface-card p-5">
          <h2 className="theme-text mb-3 text-sm font-semibold">My Stats</h2>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-xl font-semibold text-blue-600">{stats.completed}</p>
              <p className="text-xs text-slate-500">Tasks Completed</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-xl font-semibold text-amber-500">{stats.pending}</p>
              <p className="text-xs text-slate-500">Pending Tasks</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-xl font-semibold text-red-500">{stats.overdue}</p>
              <p className="text-xs text-slate-500">Tasks Overdue</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <p className="text-xl font-semibold text-purple-600">{stats.rate}%</p>
              <p className="text-xs text-slate-500">Completion Rate</p>
            </div>
          </div>
        </section>
      </div>

      {/* Tasks + sidebar row */}
      <div className="grid gap-4 xl:grid-cols-3">
        <section className="surface-card p-4 xl:col-span-2">
          <div>
            <h2 className="theme-text text-sm font-semibold">Pending Tasks</h2>
            <p className="text-xs text-slate-500">Tasks assigned to you that are pending completion.</p>
          </div>

          <div className="mt-4 divide-y divide-slate-100">
            {pendingTasks.length === 0 ? (
              <p className="py-8 text-center text-sm text-slate-500">No pending tasks. Nice work.</p>
            ) : (
              pendingTasks.map((task) => {
                const due = dueMeta(task.due_date);
                const isOpen = expandedId === task.id;
                return (
                  <div key={task.id} className="py-3">
                    <button
                      type="button"
                      className="flex w-full items-center gap-3 text-left"
                      onClick={() => setExpandedId(isOpen ? '' : task.id)}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
                        <FileText size={16} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="theme-text block truncate text-sm font-medium">{task.title}</span>
                        <span className="theme-muted block truncate text-xs">{task.description || 'No description'}</span>
                      </span>
                      <span className={`rounded-full px-2 py-1 text-xs font-semibold capitalize ${priorityClasses(task.priority)}`}>
                        {task.priority || 'Normal'}
                      </span>
                      <span
                        className={`hidden shrink-0 text-xs font-medium sm:block ${
                          due.overdue || due.dueToday ? 'text-red-500' : 'text-slate-500'
                        }`}
                      >
                        {due.label}
                      </span>
                      <span className={`rounded-full px-2 py-1 text-xs font-semibold capitalize ${statusClasses(task.status)}`}>
                        {task.status || 'pending'}
                      </span>
                      {isOpen ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                    </button>

                    {isOpen && (
                      <div className="mt-3 grid gap-3 rounded-xl bg-slate-50 p-3 sm:grid-cols-2">
                        <div>
                          <label className="block text-xs font-semibold text-slate-500">
                            Work report
                            <textarea
                              className="form-input mt-1 min-h-24"
                              value={reports[task.id] || ''}
                              onChange={(event) => setReports((current) => ({ ...current, [task.id]: event.target.value }))}
                              placeholder="Describe what happened on site..."
                            />
                          </label>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => updateTask(task, { status: 'in_progress' }, 'Task started')}
                              disabled={busyId === task.id}
                            >
                              <FileText size={14} />
                              Start
                            </button>
                            <button type="button" className="btn-primary" onClick={() => submitReport(task)} disabled={busyId === task.id}>
                              <CheckCircle2 size={14} />
                              Submit report
                            </button>
                          </div>
                        </div>

                        <div>
                          <label className="block text-xs font-semibold text-slate-500">
                            Bounce reason
                            <textarea
                              className="form-input mt-1 min-h-24"
                              value={bounceReasons[task.id] || ''}
                              onChange={(event) => setBounceReasons((current) => ({ ...current, [task.id]: event.target.value }))}
                              placeholder="Example: customer unavailable, wrong location, missing equipment..."
                            />
                          </label>
                          <button
                            type="button"
                            className="btn-secondary mt-2 text-red-600"
                            onClick={() => bounceTask(task)}
                            disabled={busyId === task.id}
                          >
                            <AlertCircle size={14} />
                            Mark bounced
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>

          {pendingTasks.length > 0 && (
            <button type="button" className="mt-3 w-full text-center text-xs font-semibold text-blue-600 hover:underline" onClick={load}>
              View all pending tasks
            </button>
          )}
        </section>

        <div className="space-y-4">
          <section className="surface-card p-4">
            <h2 className="theme-text text-sm font-semibold">My Information</h2>
            <div className="mt-3 space-y-3 text-sm">
              <div>
                <p className="text-xs font-semibold text-slate-400">Full Name</p>
                <p className="theme-text">{profile?.full_name || '—'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-400">Email</p>
                <p className="theme-text">{profile?.email || '—'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-400">Phone</p>
                <p className="theme-text">{profile?.phone || '—'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-400">Department</p>
                <p className="theme-text">{profile?.department || '—'}</p>
              </div>
            </div>
            <button type="button" className="btn-secondary mt-4 w-full justify-center text-blue-600">
              Edit Profile
            </button>
          </section>

          <section className="surface-card p-4">
            <h2 className="theme-text text-sm font-semibold">Recent Activity</h2>
            <ul className="mt-3 space-y-3">
              {tasks.slice(0, 3).map((task) => (
                <li key={task.id} className="flex items-start gap-2 text-xs">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500">
                    <Clock size={12} />
                  </span>
                  <span>
                    <span className="theme-text block font-medium">{task.title}</span>
                    <span className="capitalize text-slate-400">{task.status || 'pending'}</span>
                  </span>
                </li>
              ))}
              {tasks.length === 0 && <p className="text-xs text-slate-500">No recent activity yet.</p>}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}