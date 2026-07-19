import { Link, Outlet } from 'react-router-dom';

export function Layout() {
  return (
    <div className="min-h-screen bg-[#0b1220] text-gray-200">
      <nav className="flex gap-4 border-b border-gray-800 px-6 py-3 text-sm">
        <Link to="/" className="text-blue-400 hover:text-blue-300">Tasks</Link>
        <Link to="/approvals" className="text-blue-400 hover:text-blue-300">Approvals</Link>
        <Link to="/audit" className="text-blue-400 hover:text-blue-300">Audit</Link>
      </nav>
      <main className="px-6 py-4">
        <Outlet />
      </main>
    </div>
  );
}
