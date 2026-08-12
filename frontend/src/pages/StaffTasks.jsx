import { AlertCircle, CheckCircle2, FileText } from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

export default function StaffTasks() {
  const [tasks, setTasks] = useState([]);
  const [reports, setReports] = useState({});
  const [bounceReasons, setBounceReasons] = useState({});
  const [busyId, setBusyId] = useState('');

  const load = async () => {
    try {
      const { data } = await api.get('/staff/tasks?all=1');
      setTasks(items(data));
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to load assigned tasks');
    }
  };

  useEffect(() => {
    load();
  }, []);

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
    } catch (error) {
      toast.error(error.response?.data?.message || 'Failed to submit report');
    } finally {
      setBusyId('');
    }
  };

  const bounceTask = async (task) => {
    const reason = (bounceReasons[task.id] || '').trim();
    if (!reason) return toast.error('Add a bounce reason first');
    await updateTask(task, { bounce_reason: reason }, 'Task marked bounced');
    setBounceReasons((current) => ({ ...current, [task.id]: '' }));
  };

  return (
    <div className="space-y-4">
      <section className="surface-card p-4">
        <h1 className="page-title">My Tasks</h1>
        <p className="page-subtitle">View assigned field work, submit reports, or bounce a task with a reason.</p>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        {tasks.length === 0 ? (
          <section className="surface-card p-8 text-center text-sm text-slate-500 xl:col-span-2">No assigned tasks yet.</section>
        ) : tasks.map((task) => (
          <article key={task.id} className="surface-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="theme-text text-sm font-semibold">{task.title}</h2>
                <p className="theme-muted mt-1 text-xs">{task.description || 'No description'}</p>
              </div>
              <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold capitalize text-slate-700">{task.status || 'pending'}</span>
            </div>

            <div className="mt-4 grid gap-3">
              <label className="block text-xs font-semibold text-slate-500">
                Work report
                <textarea
                  className="form-input min-h-24"
                  value={reports[task.id] || ''}
                  onChange={(event) => setReports((current) => ({ ...current, [task.id]: event.target.value }))}
                  placeholder="Describe what happened on site..."
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-secondary" onClick={() => updateTask(task, { status: 'in_progress' }, 'Task started')} disabled={busyId === task.id}>
                  <FileText size={14} />
                  Start
                </button>
                <button type="button" className="btn-primary" onClick={() => submitReport(task)} disabled={busyId === task.id}>
                  <CheckCircle2 size={14} />
                  Submit report
                </button>
              </div>
            </div>

            <div className="mt-4 border-t border-slate-200 pt-4">
              <label className="block text-xs font-semibold text-slate-500">
                Bounce reason
                <textarea
                  className="form-input min-h-20"
                  value={bounceReasons[task.id] || ''}
                  onChange={(event) => setBounceReasons((current) => ({ ...current, [task.id]: event.target.value }))}
                  placeholder="Example: customer unavailable, wrong location, missing equipment..."
                />
              </label>
              <button type="button" className="btn-secondary mt-3 text-red-600" onClick={() => bounceTask(task)} disabled={busyId === task.id}>
                <AlertCircle size={14} />
                Mark bounced
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
