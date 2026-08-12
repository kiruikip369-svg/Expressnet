import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/axios';

function items(data) {
  return Array.isArray(data) ? data : data?.results || [];
}

export default function StaffReports() {
  const [reports, setReports] = useState([]);

  useEffect(() => {
    api.get('/staff/reports?all=1')
      .then(({ data }) => setReports(items(data)))
      .catch((error) => toast.error(error.response?.data?.message || 'Failed to load work reports'));
  }, []);

  return (
    <div className="space-y-4">
      <section className="surface-card p-4">
        <h1 className="page-title">Work Reports</h1>
        <p className="page-subtitle">A history of the field reports you have submitted.</p>
      </section>
      <section className="surface-card divide-y divide-slate-200">
        {reports.length === 0 ? (
          <p className="p-8 text-center text-sm text-slate-500">No work reports submitted yet.</p>
        ) : reports.map((report) => (
          <article key={report.id} className="p-4">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="theme-text text-sm font-semibold">{report.task_title || 'General report'}</h2>
              <span className="theme-muted text-xs">{report.created_at ? new Date(report.created_at).toLocaleString() : ''}</span>
            </div>
            <p className="theme-muted mt-2 text-sm">{report.report}</p>
          </article>
        ))}
      </section>
    </div>
  );
}
