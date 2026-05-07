import { useState } from 'react';
import { useApp } from '../context/AppContext';
import SearchRow from '../components/SearchRow';

const FILTERS = ['전체','기업금융부','시너지금융부','투자금융부'];

export default function StoragePage() {
  const { savedReports, navigate, setStorageDetail } = useApp();
  const [filter, setFilter] = useState('전체');
  const [query, setQuery] = useState('');

  const filtered = savedReports.filter(r => {
    const matchDept = filter === '전체' || r.dept === filter;
    const matchQ = !query || r.name.includes(query);
    return matchDept && matchQ;
  });

  const openDetail = (report) => {
    setStorageDetail(report);
    navigate('storage-detail', 'storage');
  };

  return (
    <div className="main">
      <div className="topbar">
        <span className="topbar-title">보관함</span>
      </div>

      <div style={{ flex:1, overflowY:'auto', padding:'16px 28px 28px', display:'flex', flexDirection:'column', gap:14 }}>
        <SearchRow placeholder="기업명을 검색하세요" types={['기업명','사업자번호']} onSearch={({value}) => setQuery(value)} />

        <div style={{ background:'var(--white)', border:'1px solid var(--border)', borderRadius:'var(--radius-md)', boxShadow:'var(--shadow-card)', overflow:'hidden', flex:1, display:'flex', flexDirection:'column' }}>
          <div className="filter-tabs">
            {FILTERS.map(f => (
              <button key={f} className={`filter-tab${filter === f ? ' active' : ''}`} onClick={() => setFilter(f)}>{f}</button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <div className="empty-panel" style={{ flex:1 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" style={{ width:44, height:44, opacity:0.25 }}>
                <polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5" rx="1"/><line x1="10" y1="12" x2="14" y2="12"/>
              </svg>
              <p>저장된 보고서가 없습니다.</p>
              <p style={{ fontSize:12, color:'var(--text-muted)' }}>여신심사보고서에서 보고서를 저장해보세요.</p>
            </div>
          ) : (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:16, padding:16, overflowY:'auto', flex:1 }}>
              {filtered.map(r => (
                <div key={r.id} style={{ border:'1px solid var(--border)', borderRadius:'var(--radius-md)', overflow:'hidden', cursor:'pointer', transition:'box-shadow 0.2s,transform 0.2s', background:'var(--white)', boxShadow:'var(--shadow-card)' }}
                  onClick={() => openDetail(r)}
                  onMouseEnter={e => { e.currentTarget.style.boxShadow='0 6px 20px rgba(0,0,0,0.1)'; e.currentTarget.style.transform='translateY(-2px)'; }}
                  onMouseLeave={e => { e.currentTarget.style.boxShadow='var(--shadow-card)'; e.currentTarget.style.transform=''; }}
                >
                  <div style={{ padding:'16px 18px 14px', display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:8 }}>
                    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
                      <span style={{ fontSize:15, fontWeight:700, color:'var(--text-primary)' }}>{r.name}</span>
                      {r.dept && <span style={{ display:'inline-flex', alignItems:'center', padding:'3px 10px', border:'1px solid var(--border)', borderRadius:20, fontSize:11.5, fontWeight:500, color:'var(--text-secondary)', background:'var(--main-bg)' }}>{r.dept}</span>}
                    </div>
                    <div style={{ display:'inline-flex', alignItems:'center', justifyContent:'center', width:36, height:36, borderRadius:'50%', fontSize:14, fontWeight:800, background:'var(--teal-light)', color:'var(--teal)', border:'2px solid var(--teal)', flexShrink:0 }}>{r.rating}</div>
                  </div>
                  <div style={{ height:1, background:'var(--border)' }} />
                  <div style={{ padding:'12px 18px', display:'flex', flexDirection:'column', gap:6 }}>
                    {[['종합 신용등급', `${r.rating} · ${r.ratingName}`],['매출액','—'],['영업이익률','—']].map(([label, val]) => (
                      <div key={label} style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
                        <span style={{ fontSize:11.5, color:'var(--text-muted)' }}>{label}</span>
                        <span style={{ fontSize:12, fontWeight:600, color:'var(--text-primary)' }}>{val}</span>
                      </div>
                    ))}
                  </div>
                  <div style={{ padding:'10px 18px', borderTop:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
                    <span style={{ fontSize:11, color:'var(--text-muted)' }}>저장일: {r.date}</span>
                    <span style={{ fontSize:11.5, fontWeight:600, color:'var(--teal)', display:'flex', alignItems:'center', gap:4 }}>
                      보고서 보기
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
