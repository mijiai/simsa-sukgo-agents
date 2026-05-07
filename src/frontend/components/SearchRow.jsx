import { useState } from 'react';

const TYPES = ['기업명', '사업자번호', '법인번호'];

export default function SearchRow({ placeholder = '기업명을 검색하세요', onSearch, types = TYPES }) {
  const [type, setType] = useState(types[0]);
  const [value, setValue] = useState('');
  const [open, setOpen] = useState(false);

  const handleSearch = () => onSearch && onSearch({ type, value });

  return (
    <div className="search-row" style={{ position: 'relative' }}>
      <div className="dropdown-wrap">
        <div className="search-selector" onClick={() => setOpen(o => !o)}>
          <span>{type}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </div>
        {open && (
          <div className="dropdown">
            {types.map(t => (
              <div key={t} className="dropdown-item" onClick={() => { setType(t); setOpen(false); }}>{t}</div>
            ))}
          </div>
        )}
      </div>

      <input
        className="search-input"
        type="text"
        placeholder={type === '기업명' ? placeholder : `${type}를 입력하세요`}
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && handleSearch()}
      />

      <button className="search-btn" onClick={handleSearch} title="검색">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7"/>
          <line x1="16.5" y1="16.5" x2="22" y2="22"/>
        </svg>
      </button>
    </div>
  );
}
