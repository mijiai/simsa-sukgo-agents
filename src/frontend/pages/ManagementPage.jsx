import { useState } from 'react';
import { useApp } from '../context/AppContext';
import SearchRow from '../components/SearchRow';
import RegisterModal from '../components/RegisterModal';

const FILTERS = ['전체','기업금융부','시너지금융부','투자금융부'];

export default function ManagementPage() {
  const { mgmtCompanies, navigate, setDetailCompany } = useApp();
  const [filter, setFilter] = useState('전체');
  const [modalOpen, setModalOpen] = useState(false);

  const filtered = mgmtCompanies.filter(c => filter === '전체' || c.dept === filter);

  const openDetail = (company) => {
    setDetailCompany(company);
    navigate('mgmt-detail', 'mgmt');
  };

  return (
    <div className="main">
      <div className="topbar">
        <span className="topbar-title">사후관리</span>
        <button className="teal-btn" onClick={() => setModalOpen(true)}>기업 등록</button>
      </div>

      <div className="mgmt-body" style={{ flex:1, overflowY:'auto', padding:'16px 28px 28px', display:'flex', flexDirection:'column', gap:14 }}>
        <SearchRow placeholder="기업명을 검색하세요" types={['기업명','사업자번호']} />

        <div style={{ background:'var(--white)', border:'1px solid var(--border)', borderRadius:'var(--radius-md)', boxShadow:'var(--shadow-card)', overflow:'hidden', flex:1, display:'flex', flexDirection:'column' }}>
          {/* 필터 탭 */}
          <div className="filter-tabs">
            {FILTERS.map(f => (
              <button key={f} className={`filter-tab${filter === f ? ' active' : ''}`} onClick={() => setFilter(f)}>{f}</button>
            ))}
          </div>

          {/* 그리드 */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:16, padding:16, overflowY:'auto', flex:1 }}>
            {filtered.map(company => (
              <div key={company.id} className="mgmt-card" onClick={() => openDetail(company)}>
                <div className="mgmt-card-header" style={{ backgroundColor: company.color, display:'flex', alignItems:'flex-start', justifyContent:'space-between', padding:'14px 18px' }}>
                  <div style={{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
                    <span style={{ fontSize:15, fontWeight:700, color:'#1A1D23' }}>{company.name}</span>
                    {company.dept && <span className="dept-tag">{company.dept}</span>}
                  </div>
                  <div style={{ textAlign:'right', fontSize:11.5, lineHeight:1.7, color:'rgba(0,0,0,0.5)', flexShrink:0 }}>
                    등록일: {company.regDate}<br/>배치 주기: {company.cycle}
                  </div>
                </div>
                <div>
                  {company.news.map((n, i) => (
                    <div key={i} style={{ display:'flex', alignItems:'center', gap:10, padding:'11px 16px', borderTop:'1px solid var(--border)' }}>
                      <span className={`badge badge-${n.type}`}>{n.type === 'pos' ? '긍정' : '부정'}</span>
                      <div style={{ flex:1, minWidth:0 }}>
                        <div style={{ fontSize:13, fontWeight:600, color:'var(--text-primary)', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', marginBottom:2 }}>{n.title}</div>
                        <div style={{ fontSize:12, color:'var(--text-secondary)', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{n.desc}</div>
                      </div>
                      <div style={{ display:'flex', alignItems:'center', gap:10, flexShrink:0, fontSize:11.5, color:'var(--text-muted)', whiteSpace:'nowrap' }}>
                        <span>{n.date}</span><span>{n.source}</span>
                      </div>
                    </div>
                  ))}
                  {company.news.length === 0 && (
                    <div style={{ padding:'16px', fontSize:12, color:'var(--text-muted)', borderTop:'1px solid var(--border)' }}>최신 정보를 불러오는 중입니다.</div>
                  )}
                </div>
              </div>
            ))}
            {/* 빈 슬롯 */}
            {Array.from({ length: Math.max(0, 4 - filtered.length) }).map((_, i) => (
              <div key={`empty-${i}`} className="mgmt-empty" />
            ))}
          </div>
        </div>
      </div>

      <RegisterModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}
