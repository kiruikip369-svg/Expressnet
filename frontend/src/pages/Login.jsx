import { useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import toast from 'react-hot-toast';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';

function getMessage(error) {
  return error.response?.data?.message || error.message || 'Something went wrong. Please try again.';
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ email: '', password: '' });
  const [challengeId, setChallengeId] = useState('');
  const [verificationCode, setVerificationCode] = useState('');
  const [errors, setErrors] = useState({});
  const [showPassword, setShowPassword] = useState(false);
  const [resending, setResending] = useState(false);

  const update = (event) => {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));
    setErrors((current) => ({ ...current, [event.target.name]: '' }));
  };

  const validate = () => {
    if (challengeId) {
      if (verificationCode.length !== 6) {
        toast.error('Enter the 6-digit verification code');
        return false;
      }
      return true;
    }
    const nextErrors = {};
    if (!form.email.trim()) nextErrors.email = 'Email is required';
    if (!form.password) nextErrors.password = 'Password is required';
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!validate()) return;

    setLoading(true);
    try {
      const payload = challengeId ? { challenge_id: challengeId, code: verificationCode } : form;
      const { data } = await api.post('/auth/login', payload);
      if (data.requires_two_step) {
        setChallengeId(data.challenge_id);
        toast.success(data.message || 'Verification code sent');
        return;
      }
      login(data.token, data.tenant);
      toast.success('Welcome back');
      navigate(location.state?.from?.pathname || '/dashboard', { replace: true });
    } catch (error) {
      toast.error(getMessage(error));
    } finally {
      setLoading(false);
    }
  };

  const resendVerification = async () => {
    if (!challengeId || resending) return;
    setResending(true);
    try {
      const { data } = await api.post('/auth/login', { resend_challenge_id: challengeId });
      toast.success(data.message || 'Verification code resent');
    } catch (error) {
      toast.error(getMessage(error));
    } finally {
      setResending(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-10">
      <section className="w-full max-w-md rounded-lg bg-white p-8 shadow-soft ring-1 ring-slate-200">
        <div className="mb-8">
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600">Expressnetbilling</p>
          <h1 className="mt-2 text-2xl font-bold text-slate-900">Sign in to your tenant account</h1>
        </div>

        <form className="space-y-5" onSubmit={submit}>
          {!challengeId ? (
            <>
              <div>
                <label className="form-label" htmlFor="email">Email</label>
                <input id="email" name="email" type="email" className="form-input" value={form.email} onChange={update} />
                {errors.email && <p className="form-error">{errors.email}</p>}
              </div>

              <div>
                <label className="form-label" htmlFor="password">Password</label>
                <div className="relative">
              <input
                id="password"
                name="password"
                type={showPassword ? 'text' : 'password'}
                className="form-input pr-11"
                value={form.password}
                onChange={update}
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-100"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
                </div>
                {errors.password && <p className="form-error">{errors.password}</p>}
              </div>
            </>
          ) : (
            <div>
              <label className="form-label" htmlFor="verificationCode">Verification code</label>
              <input
                id="verificationCode"
                className="form-input text-center text-lg tracking-[0.35em]"
                inputMode="numeric"
                maxLength={6}
                value={verificationCode}
                onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                autoFocus
              />
              <p className="mt-2 text-xs text-slate-500">Enter the 6-digit code sent to your email.</p>
            </div>
          )}

          <button type="submit" className="btn-primary w-full" disabled={loading}>
            {loading ? 'Signing in...' : challengeId ? 'Verify and login' : 'Login'}
          </button>
          {challengeId && (
            <button type="button" className="btn-secondary w-full justify-center" onClick={resendVerification} disabled={loading || resending}>
              {resending ? 'Resending...' : 'Resend verification email'}
            </button>
          )}
        </form>

        <p className="mt-6 text-center text-sm text-slate-600">
          New tenant?{' '}
          <Link className="font-semibold text-blue-600 hover:text-blue-700" to="/register">
            Create an account
          </Link>
        </p>
      </section>
    </main>
  );
}
