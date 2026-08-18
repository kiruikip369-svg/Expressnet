import { useState } from 'react';
import toast from 'react-hot-toast';
import { Link, useNavigate } from 'react-router-dom';
import api from '../api/axios';

const initialForm = {
  business_name: '',
  owner_name: '',
  email: '',
  phone: '',
  password: '',
};

const labels = {
  business_name: 'Business name',
  owner_name: 'Owner name',
  email: 'Email',
  phone: 'Phone',
  password: 'Password',
};

const trialDays = import.meta.env.VITE_TENANT_TRIAL_DAYS || '14';

function Field({ name, type = 'text', value, error, onChange }) {
  return (
    <div>
      <label className="form-label" htmlFor={name}>{labels[name]}</label>
      <input id={name} name={name} type={type} className="form-input" value={value} onChange={onChange} />
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const [form, setForm] = useState(initialForm);
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);

  const update = (event) => {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    const nextErrors = {};
    Object.entries(form).forEach(([key, value]) => {
      if (!String(value).trim()) nextErrors[key] = `${labels[key]} is required`;
    });
    if (form.password && form.password.length < 6) nextErrors.password = 'Password must be at least 6 characters';
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!validate()) return;

    setLoading(true);
    try {
      const { data } = await api.post('/auth/register', {
        ...form,
      });
      toast.success(data.message || 'Registration successful. Please wait for admin activation.');
      navigate('/login');
    } catch (error) {
      toast.error(error.response?.data?.message || 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-10">
      <section className="mx-auto max-w-5xl rounded-lg bg-white p-6 shadow-soft ring-1 ring-slate-200 sm:p-8">
        <div className="mb-8">
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600">Expressnetbilling</p>
          <h1 className="mt-2 text-2xl font-bold text-slate-900">Register your hotspot business</h1>
          <p className="mt-2 text-sm text-slate-500">
            Create your tenant account and billing workspace. New tenants get a {trialDays}-day grace period before system payment is required.
          </p>
        </div>

        <form className="space-y-8" onSubmit={submit}>
          <div>
            <h2 className="mb-4 text-base font-bold text-slate-900">Business details</h2>
            <div className="grid gap-4 md:grid-cols-2">
              <Field name="business_name" value={form.business_name} error={errors.business_name} onChange={update} />
              <Field name="owner_name" value={form.owner_name} error={errors.owner_name} onChange={update} />
              <Field name="email" type="email" value={form.email} error={errors.email} onChange={update} />
              <Field name="phone" value={form.phone} error={errors.phone} onChange={update} />
              <Field name="password" type="password" value={form.password} error={errors.password} onChange={update} />
            </div>
          </div>
          <div className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-6 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-600">
              Already registered?{' '}
              <Link className="font-semibold text-blue-600 hover:text-blue-700" to="/login">
                Login
              </Link>
            </p>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? 'Creating account...' : 'Create account'}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
