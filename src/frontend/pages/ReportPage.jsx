import { useState } from 'react';
import { useApp } from '../context/AppContext';

const TABS = ['요약','재무분석','위험분석','전체보고서'];

export default function ReportPage({ onToast }) {
  const { reportCompany, reportSaved, saveReport, navigate } = useApp();
  const [activeTab, setActiveTab] = useState(0);

  const handleSave = () => {
    const ok = saveReport();
    if (ok) onToast('보관함에 저장되었습니다 ✓');
  };

  return (
    <div className="main">
      {/* 상단 */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'flex-end', padding:'16px 28px', flexShrink:0 }}>
        <button className="teal-btn" onClick={() => navigate('home','yeosin')}>다른 기업 분석하기</button>
      </div>

      {/* 본문 */}
      <div style={{ flex:1, overflowY:'auto', padding:'0 28px', display:'flex', flexDirection:'column', minHeight:0 }}>
        <h2 style={{ fontSize:22, fontWeight:700, color:'var(--text-primary)', letterSpacing:'-0.4px', marginBottom:16 }}>{reportCompany}</h2>
        <div className="report-card">
          <div className="tab-bar">
            <div className="tabs">
              {TABS.map((t, i) => (
                <button key={t} className={`tab${activeTab === i ? ' active' : ''}`} onClick={() => setActiveTab(i)}>{t}</button>
              ))}
            </div>
            <div className="tab-actions">
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

          <div className="tab-content">
            {activeTab === 0 && (
              <div>
                <div className="rating-row">
                  <div className="rating-badge">B+</div>
                  <div className="rating-info">
                    <span className="rating-label-text">종합 신용등급</span>
                    <span className="rating-name">양호</span>
                  </div>
                </div>
                <div className="summary-grid">
                  <div className="metric-card"><div className="metric-label">매출액</div><div className="metric-value">—</div><div className="metric-sub">전년 대비 —</div></div>
                  <div className="metric-card"><div className="metric-label">영업이익률</div><div className="metric-value">—</div><div className="metric-sub">업종 평균 대비 —</div></div>
                  <div className="metric-card"><div className="metric-label">부채비율</div><div className="metric-value">—</div><div className="metric-sub">업종 평균 대비 —</div></div>
                </div>
                <div className="section-title">종합 의견</div>
                <p className="summary-text">분석 중입니다...</p>
              </div>
            )}
            {activeTab === 1 && <div className="empty-panel"><p>재무분석 데이터를 불러오는 중입니다.</p></div>}
            {activeTab === 2 && <div className="empty-panel"><p>위험분석 데이터를 불러오는 중입니다.</p></div>}
            {activeTab === 3 && <div className="empty-panel"><p>전체 보고서를 준비 중입니다.</p></div>}
          </div>
        </div>
      </div>

      {/* 하단 저장 버튼 */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'flex-end', padding:'14px 28px', flexShrink:0, borderTop:'1px solid var(--border)', background:'var(--white)', gap:10 }}>
        <button className="action-btn" onClick={() => navigate('home','yeosin')}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          홈으로
        </button>
        <button
          onClick={handleSave}
          style={{
            display:'inline-flex', alignItems:'center', gap:7, padding:'10px 22px',
            background: reportSaved ? '#6B7280' : 'var(--teal)',
            color:'white', border:'none', borderRadius:'var(--radius-sm)',
            fontFamily:'inherit', fontSize:13, fontWeight:600,
            cursor: reportSaved ? 'default' : 'pointer',
            transition:'all 0.2s',
            boxShadow: reportSaved ? 'none' : 'var(--shadow-btn)',
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width:15, height:15 }}>
            {reportSaved
              ? <polyline points="20 6 9 17 4 12"/>
              : <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></>
            }
          </svg>
          {reportSaved ? '저장 완료' : '보관함에 저장'}
        </button>
      </div>
    </div>
  );
}
