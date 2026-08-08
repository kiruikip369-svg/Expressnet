import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { canAccessPage, firstAllowedPath, pageForPath } from '../utils/permissions';

export default function ProtectedRoute() {
  const { isAuthenticated, tenant } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  const pageKey = pageForPath(location.pathname);
  if (!canAccessPage(tenant, pageKey)) {
    return <Navigate to={firstAllowedPath(tenant)} replace />;
  }

  return <Outlet />;
}
