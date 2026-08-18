import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';
import {
  Search, ClipboardList, Filter, Calendar, X, Loader2, CheckCircle2,
} from 'lucide-react';

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

function normalizeTask(task = {}) {
  return {
    ...task,
    title: task.title || task.task_title || task.type || task.task_type || 'Assigned task',
    description: task.description || task.notes || task.customer_name || '',
    priority: task.priority || 'medium',
    status: task.status || 'pending',
    assigned_by: task.assigned_by || {
      name: task.assigned_to_name || task.staff_name || '',
      role: task.assigned_to_role || task.staff_role || '',
    },
  };
}

const PRIORITY_STYLES = {
  high: 'bg-rose-50 text-rose-600',
  medium: 'bg-orange-50 text-orange-600',
  low: 'bg-emerald-50 text-emerald-600',
};

const STATUS_STYLES = {
  pending: 'bg-amber-50 text-amber-600',
  in_progress: 'bg-blue-50 text-blue-600',
  complete: 'bg-emerald-50 text-emerald-600',
  bounced: 'bg-rose-50 text-rose-600',
};

const STATUS_LABELS = {
  pending: 'Pending',
  in_progress: 'In Progress',
  complete: 'Complete',
  bounced: 'Bounced',
};

const PAGE_SIZE = 5;

function dueMeta(dueDate) {
  if (!dueDate) return { label: '', className: 'theme-muted' };
  const due = new Date(dueDate);
  const today = new Date();
  due.setHours(0, 0, 0, 0);
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((due - today) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) {
    return {
      label: `${Math.abs(diffDays)} day${Math.abs(diffDays) === 1 ? '' : 's'} overdue`,
      className: 'text-rose-600',
    };
  }
  if (diffDays === 0) return { label: 'Today', className: 'text-rose-600' };
  return { label: `${diffDays} day${diffDays === 1 ? '' : 's'} left`, className: 'text-emerald-600' };
}

function initials(name = '') {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('');
}

function ReportModal({ task, onClose, onSubmitted }) {
  const [report, setReport] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!report.trim()) {
      toast.error('Please write a report before submitting');
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post('/staff/reports', {
        task_id: task.id,
        task_title: task.title || task.task_title || '',
        report: report.trim(),
      });
      await api.patch(`/staff/tasks/${task.id}`, { work_report: report.trim(), status: 'complete' });
      toast.success('Work report submitted');
      onSubmitted(data?.report || { task_id: task.id, report: report.trim() });
      onClose();
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit report');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="surface-card w-full max-w-lg p-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="theme-text text-base font-semibold">Submit Report</h2>
            <p className="theme-muted text-sm">{task.title || task.task_title}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <textarea
          value={report}
          onChange={(e) => setReport(e.target.value)}
          rows={6}
          placeholder="Describe the work you completed for this task..."
          className="mt-4 w-full rounded-lg border border-slate-200 p-3 text-sm focus:border-blue-400 focus:outline-none"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Submit Report
          </button>
        </div>
      </div>
    </div>
  );
}

export default function StaffReports() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('pending');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [showFilter, setShowFilter] = useState(false);
  const [sortByDue, setSortByDue] = useState(false);
  const [page, setPage] = useState(1);
  const [activeTask, setActiveTask] = useState(null);

  const loadTasks = () => {
    setLoading(true);
    api
      .get('/staff/tasks')
      .then(({ data }) => setTasks(items(data).map(normalizeTask)))
      .catch((error) => toast.error(error.response?.data?.message || 'Failed to load tasks'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadTasks();
  }, []);

  const pendingCount = tasks.filter((t) => t.status === 'pending').length;
  const inProgressCount = tasks.filter((t) => t.status === 'in_progress').length;
  const completeCount = tasks.filter((t) => ['complete', 'completed'].includes(t.status)).length;

  const filteredTasks = useMemo(() => {
    let list = tasks.filter((t) => (activeTab === 'complete' ? ['complete', 'completed'].includes(t.status) : t.status === activeTab));
    if (priorityFilter !== 'all') {
      list = list.filter((t) => (t.priority || '').toLowerCase() === priorityFilter);
    }
    if (sortByDue) {
      list = [...list].sort((a, b) => new Date(a.due_date || 0) - new Date(b.due_date || 0));
    }
    return list;
  }, [tasks, activeTab, priorityFilter, sortByDue]);

  useEffect(() => {
    setPage(1);
  }, [activeTab, priorityFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredTasks.length / PAGE_SIZE));
  const pageTasks = filteredTasks.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const handleReportSubmitted = (report) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === report.task_id ? normalizeTask({ ...t, work_report: report.report, reported_at: report.created_at, status: 'complete' }) : t))
    );
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="surface-card flex items-center gap-3 p-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-orange-50">
            <Search className="h-6 w-6 text-orange-500" />
          </div>
          <div>
            <p className="text-2xl font-semibold text-orange-500">{inProgressCount}</p>
            <p className="theme-text text-sm font-medium">In Progress</p>
            <p className="theme-muted text-xs">Tasks currently in progress</p>
          </div>
        </div>
        <div className="surface-card flex items-center gap-3 p-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-50">
            <ClipboardList className="h-6 w-6 text-blue-600" />
          </div>
          <div>
            <p className="text-2xl font-semibold text-blue-600">{pendingCount}</p>
            <p className="theme-text text-sm font-medium">Pending</p>
            <p className="theme-muted text-xs">Tasks awaiting action</p>
          </div>
        </div>
      </div>

      <div className="surface-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-6 border-b border-transparent text-sm font-medium">
            <button
              onClick={() => setActiveTab('pending')}
              className={`pb-2 ${activeTab === 'pending' ? 'border-b-2 border-blue-600 text-blue-600' : 'theme-muted'}`}
            >
              Pending Tasks
            </button>
            <button
              onClick={() => setActiveTab('in_progress')}
              className={`pb-2 ${activeTab === 'in_progress' ? 'border-b-2 border-blue-600 text-blue-600' : 'theme-muted'}`}
            >
              In Progress Tasks ({inProgressCount})
            </button>
            <button
              onClick={() => setActiveTab('complete')}
              className={`pb-2 ${activeTab === 'complete' ? 'border-b-2 border-blue-600 text-blue-600' : 'theme-muted'}`}
            >
              Submitted Reports ({completeCount})
            </button>
          </div>
          <div className="relative flex items-center gap-2">
            <button
              onClick={() => setShowFilter((v) => !v)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
            >
              <Filter className="h-4 w-4" /> Filter
            </button>
            <button
              onClick={() => setSortByDue((v) => !v)}
              className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm ${
                sortByDue ? 'border-blue-400 text-blue-600' : 'border-slate-200 text-slate-600 hover:bg-slate-50'
              }`}
              title="Sort by due date"
            >
              <Calendar className="h-4 w-4" />
            </button>
            {showFilter && (
              <div className="absolute right-0 top-10 z-10 w-40 rounded-lg border border-slate-200 bg-white p-2 shadow-lg">
                {['all', 'high', 'medium', 'low'].map((p) => (
                  <button
                    key={p}
                    onClick={() => {
                      setPriorityFilter(p);
                      setShowFilter(false);
                    }}
                    className={`block w-full rounded-md px-2 py-1.5 text-left text-sm capitalize hover:bg-slate-50 ${
                      priorityFilter === p ? 'font-semibold text-blue-600' : 'text-slate-600'
                    }`}
                  >
                    {p === 'all' ? 'All priorities' : p}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="theme-muted text-xs uppercase tracking-wide">
                <th className="pb-2 font-medium">Task</th>
                <th className="pb-2 font-medium">Priority</th>
                <th className="pb-2 font-medium">Due Date</th>
                <th className="pb-2 font-medium">Assigned Staff</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-sm text-slate-500">
                    Loading tasks...
                  </td>
                </tr>
              ) : pageTasks.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-sm text-slate-500">
                    No tasks here.
                  </td>
                </tr>
              ) : (
                pageTasks.map((task) => {
                  const due = dueMeta(task.due_date);
                  const assignedBy = task.assigned_by || {};
                  const assignedByName = typeof assignedBy === 'string' ? assignedBy : assignedBy.name;
                  const assignedByRole = typeof assignedBy === 'string' ? '' : assignedBy.role;
                  return (
                    <tr key={task.id}>
                      <td className="py-3 pr-4">
                        <p className="theme-text font-semibold">{task.title || task.task_title}</p>
                        <p className="theme-muted text-xs">
                          {task.description}
                          {task.client ? ` for ${task.client}` : ''}
                        </p>
                      </td>
                      <td className="py-3 pr-4">
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-medium capitalize ${
                            PRIORITY_STYLES[(task.priority || '').toLowerCase()] || 'bg-slate-100 text-slate-600'
                          }`}
                        >
                          {task.priority || '-'}
                        </span>
                      </td>
                      <td className="py-3 pr-4">
                        <p className="theme-text">{task.due_date ? new Date(task.due_date).toLocaleDateString() : '-'}</p>
                        {due.label && <p className={`text-xs ${due.className}`}>{due.label}</p>}
                      </td>
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2">
                          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-purple-100 text-xs font-semibold text-purple-600">
                            {initials(assignedByName)}
                          </span>
                          <div>
                            <p className="theme-text text-xs font-medium">{assignedByName || '-'}</p>
                            {assignedByRole && <p className="theme-muted text-xs">{assignedByRole}</p>}
                          </div>
                        </div>
                      </td>
                      <td className="py-3 pr-4">
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                            STATUS_STYLES[task.status] || 'bg-slate-100 text-slate-600'
                          }`}
                        >
                          {STATUS_LABELS[task.status] || task.status}
                        </span>
                      </td>
                      <td className="py-3">
                        {task.work_report ? (
                          <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                            <CheckCircle2 className="h-4 w-4" /> Report Submitted
                          </span>
                        ) : (
                          <button
                            onClick={() => setActiveTask(task)}
                            className="rounded-lg border border-blue-200 px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50"
                          >
                            Submit Report
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex items-center justify-between text-sm">
          <p className="theme-muted">
            Showing {filteredTasks.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1} to{' '}
            {Math.min(page * PAGE_SIZE, filteredTasks.length)} of {filteredTasks.length} tasks
          </p>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs disabled:opacity-40"
            >
              ‹
            </button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`rounded-md px-2.5 py-1 text-xs ${
                  p === page ? 'bg-blue-600 text-white' : 'border border-slate-200 text-slate-600'
                }`}
              >
                {p}
              </button>
            ))}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs disabled:opacity-40"
            >
              ›
            </button>
          </div>
        </div>
      </div>

      {activeTask && (
        <ReportModal task={activeTask} onClose={() => setActiveTask(null)} onSubmitted={handleReportSubmitted} />
      )}
    </div>
  );
}

