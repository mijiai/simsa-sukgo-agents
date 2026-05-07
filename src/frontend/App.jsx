import { useState, useEffect } from 'react';
import { AppProvider, useApp } from './context/AppContext';
import Sidebar from './components/Sidebar';
import HomePage from './pages/HomePage';
import ReportPage from './pages/ReportPage';
import ManagementPage from './pages/ManagementPage';
import ManagementDetailPage from './pages/ManagementDetailPage';
import StoragePage from './pages/StoragePage';
import StorageDetailPage from './pages/StorageDetailPage';
import './styles/global.css';

function Router() {
  const { page } = useApp();
  const [toast, setToast] = useState({ msg: '', show: false });

  const showToast = (msg) => {
    setToast({ msg, show: true });
    setTimeout(() => setToast(t => ({ ...t, show: false })), 2500);
  };

  const pageMap = {
    'home':           <HomePage />,
    'report':         <ReportPage onToast={showToast} />,
    'mgmt':           <ManagementPage />,
    'mgmt-detail':    <ManagementDetailPage />,
    'storage':        <StoragePage />,
    'storage-detail': <StorageDetailPage onToast={showToast} />,
  };

  return (
    <div className="app">
      <Sidebar />
      {pageMap[page] || <HomePage />}

      {/* Toast */}
      <div className={`toast${toast.show ? ' show' : ''}`}>{toast.msg}</div>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Router />
    </AppProvider>
  );
}
