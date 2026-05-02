import { useApp } from '../context/AppContext';
import cityIcon from '../assets/building.png';

const NAV_ITEMS = [
  {
    id: 'yeosin', page: 'home', label: '여신심사보고서',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="9" y1="13" x2="15" y2="13"/>
        <line x1="9" y1="17" x2="12" y2="17"/>
      </svg>
    ),
  },
  {
    id: 'mgmt', page: 'mgmt', label: '사후관리',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 11l3 3L22 4"/>
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
      </svg>
    ),
  },
  {
    id: 'storage', page: 'storage', label: '보관함',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="21 8 21 21 3 21 3 8"/>
        <rect x="1" y="3" width="22" height="5" rx="1"/>
        <line x1="10" y1="12" x2="14" y2="12"/>
      </svg>
    ),
  },
];

export default function Sidebar() {
  const { navigate, activeNav } = useApp();

  return (
    <aside className="sidebar">
      {/* 홈 버튼 (city 아이콘) */}
      <button className="home-btn" title="홈으로" onClick={() => navigate('home', 'yeosin')}>
        <img src={cityIcon} alt="홈" width={22} height={22} style={{ display:'block', objectFit:'contain' }} />
      </button>

      <nav className="nav">
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            className={`nav-item${activeNav === item.id ? ' active' : ''}`}
            onClick={() => navigate(item.page, item.id)}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">MIZiAi</div>
    </aside>
  );
}
