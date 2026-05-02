import { useState } from 'react';
import { useApp } from '../context/AppContext';
import RegisterModal from '../components/RegisterModal';

export default function ManagementDetailPage() {
  const { detailCompany, navigate } = useApp();
  const [activeTab, setActiveTab] = useState('news');
  const [modalOpen, setModalOpen] = useState(false);
  const company = detailCompany;

  if (!company) return null;

  const newsItems = company.news?.length > 0 ? company.news : [
    { type:'neg', title:'"삼성전자 파업, 수십조 피해 넘어 공급망 회복 불가 훼손"', desc:'삼성전자 노조가 내달 파업을 예고한 가운데 이로 인한 타격이 단순히 수십조원이라는 막대한 금액을 넘어 돌이킬 수 없는 신뢰와 공급망 훼손으로 이어질 수 있다는 학계의 경고가 나왔다.', date:'2026-04-01', source:'뉴스' },
    { type:'neg', title:'"삼성전자 파업, 수십조 피해 넘어 공급망 회복 불가 훼손"', desc:'삼성전자 노조가 내달 파업을 예고한 가운데 이로 인한 타격이 단순히 수십조원이라는 막대한 금액을 넘어 돌이킬 수 없는 신뢰와 공급망 훼손으로 이어질 수 있다는 학계의 경고가 나왔다.', date:'2026-04-01', source:'뉴스' },
  ];

  return (
    <div className="main">
      <div className="topbar">
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <button onClick={() => navigate('mgmt','mgmt')} style={{ background:'none', border:'none', cursor:'pointer', display:'flex', alignItems:'center', color:'var(--text-muted)' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <span className="topbar-title">{company.name}</span>
        </div>
        <button className="teal-btn" onClick={() => setModalOpen(true)}>기업 등록</button>
      </div>

      <div style={{ flex:1, overflowY:'auto', padding:'16px 28px 28px', display:'flex', flexDirection:'column', gap:14 }}>
        {/* 탭 */}
        <div style={{ display:'flex', background:'var(--white)', border:'1px solid var(--border)', borderRadius:'var(--radius-md)', padding:'0 16px', flexShrink:0, boxShadow:'var(--shadow-card)' }}>
          {[['news','최근 뉴스'],['lawsuit','소송내역']].map(([id, label]) => (
            <button key={id} className={`tab${activeTab === id ? ' active' : ''}`} style={{ borderLeft:'none', borderRight:'none', borderTop:'none' }} onClick={() => setActiveTab(id)}>{label}</button>
          ))}
        </div>

        {/* 콘텐츠 */}
        {activeTab === 'news' && (
          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            {newsItems.map((n, i) => (
              <div key={i} style={{ background:'var(--white)', border:'1px solid var(--border)', borderRadius:'var(--radius-md)', padding:'18px 20px', boxShadow:'var(--shadow-card)', display:'flex', alignItems:'flex-start', gap:16 }}>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:14, fontWeight:700, color:'var(--text-primary)', marginBottom:6 }}>{n.title}</div>
                  <div style={{ fontSize:13, lineHeight:1.7, color:'var(--text-secondary)' }}>{n.desc}</div>
                  <div style={{ fontSize:11.5, color:'var(--text-muted)', marginTop:8 }}>{n.date} · {n.source}</div>
                </div>
                <div style={{ flexShrink:0, marginTop:2 }}>
                  <span className={`badge badge-${n.type}`} style={{ padding:'5px 14px', fontSize:12 }}>{n.type === 'pos' ? '긍정' : '부정'}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {activeTab === 'lawsuit' && (
          <div className="empty-panel" style={{ minHeight:200 }}>
            <p>소송 내역이 없습니다.</p>
          </div>
        )}
      </div>

      <RegisterModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}
