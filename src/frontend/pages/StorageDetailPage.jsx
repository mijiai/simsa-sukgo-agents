import { useState } from 'react';
import { useApp } from '../context/AppContext';

const TABS = ['요약','재무분석','위험분석','전체보고서'];

export default function StorageDetailPage({ onToast }) {
  const { storageDetail, deleteStoredReport, navigate } = useApp();
  const [activeTab, setActiveTab] = useState(0);

  if (!storageDetail) return null;
  const r = storageDetail;

  const handleDelete = () => {
    deleteStoredReport(r.id);
    navigate('storage', 'storage');
    onToast('보고서가 삭제되었습니다.');
  };

  return (
    <div className="main">
      {/* 상단 */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'16px 28px', flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <button onClick={() => navigate('storage','storage')} style={{ background:'none', border:'none', cursor:'pointer', display:'flex', alignItems:'center', color:'var(--text-muted)' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <span className="topbar-title">{r.name}</span>
          {r.dept && (
            <span style={{ display:'inline-flex', alignItems:'center', padding:'3px 10px', border:'1px solid var(--border)', borderRadius:20, fontSize:11.5, fontWeight:500, color:'var(--text-secondary)', background:'var(--main-bg)' }}>{r.dept}</span>
          )}
        </div>
        <div style={{ display:'flex', gap:8 }}>
          <button className="action-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            다운로드
          </button>
          <button className="action-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            복사하기
          </button>
        </div>
      </div>

      {/* 보고서 */}
      <div style={{ flex:1, overflowY:'auto', padding:'0 28px', display:'flex', flexDirection:'column', minHeight:0 }}>
        <div className="report-card">
          <div className="tab-bar">
            <div className="tabs">
              {TABS.map((t, i) => (
                <button key={t} className={`tab${activeTab === i ? ' active' : ''}`} onClick={() => setActiveTab(i)}>{t}</button>
              ))}
            </div>
          </div>
          <div className="tab-content">
            {activeTab === 0 && (
              <div>
                <div className="rating-row">
                  <div className="rating-badge">{r.rating}</div>
                  <div className="rating-info">
                    <span className="rating-label-text">종합 신용등급</span>
                    <span className="rating-name">{r.ratingName}</span>
                  </div>
                </div>
                <div className="summary-grid">
                  <div className="metric-card"><div className="metric-label">매출액</div><div className="metric-value">—</div><div className="metric-sub">전년 대비 —</div></div>
                  <div className="metric-card"><div className="metric-label">영업이익률</div><div className="metric-value">—</div><div className="metric-sub">업종 평균 대비 —</div></div>
                  <div className="metric-card"><div className="metric-label">부채비율</div><div className="metric-value">—</div><div className="metric-sub">업종 평균 대비 —</div></div>
                </div>
                <div className="section-title">종합 의견</div>
                <p className="summary-text">{r.name}의 저장된 여신심사 보고서입니다. (저장일: {r.date})</p>
              </div>
            )}
            {activeTab > 0 && <div className="empty-panel"><p>{TABS[activeTab]} 데이터</p></div>}
          </div>
        </div>
      </div>

      {/* 하단 */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'14px 28px', flexShrink:0, borderTop:'1px solid var(--border)', background:'var(--white)' }}>
        <span style={{ fontSize:12, color:'var(--text-muted)' }}>저장일: {r.date}</span>
        <button className="action-btn" onClick={handleDelete} style={{ color:'#EF4444', borderColor:'#FECACA' }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
          삭제
        </button>
      </div>
    </div>
  );
}
