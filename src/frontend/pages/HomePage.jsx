import { useState, useRef } from 'react';
import { useApp } from '../context/AppContext';
import dandiImg from '../assets/simsasookgo_dandi.png';

export default function HomePage() {
  const { createReport } = useApp();
  const [type, setType] = useState('기업명');
  const [company, setCompany] = useState('');
  const [memo, setMemo] = useState('');
  const [file, setFile] = useState(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const fileRef = useRef();

  const handleSend = () => {
    if (!company.trim()) return;
    createReport(company.trim());
  };

  return (
    <div className="main">
      <div className="home-topbar" style={{ display:'flex', alignItems:'center', padding:'20px 36px 0', flexShrink:0 }}>
        <span className="topbar-title">심사숙고</span>
      </div>

      <div className="content" style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'0 60px 32px' }}>
        {/* 마스코트 */}
        <div style={{ marginBottom:16, animation:'float 3.6s ease-in-out infinite' }}>
          <img src={dandiImg} alt="단디" style={{ width:'clamp(230px,16vh,200px)', height:'clamp(230px,16vh,200px)', objectFit:'contain' }} />
        </div>

        <p style={{ fontSize:'clamp(15px,1.3vw,18px)', fontWeight:700, color:'var(--text-primary)', marginBottom:28, letterSpacing:'-0.2px', textAlign:'center' }}>
          AI로 심사숙고한 여신심사 보고서를 만들어보세요
        </p>

        {/* 입력 카드 */}
        <div className="input-card" style={{ width:'100%', maxWidth:700, background:'var(--white)', border:'1px solid var(--border)', borderRadius:'var(--radius-lg)', boxShadow:'var(--shadow-card)' }}>
          <div style={{ display:'flex', borderBottom:'1px solid var(--border)' }}>
            {/* 타입 드롭다운 */}
            <div style={{ position:'relative' }}>
              <div
                style={{ display:'flex', alignItems:'center', gap:6, padding:'13px 15px', borderRight:'1px solid var(--border)', cursor:'pointer', minWidth:106, userSelect:'none', transition:'background 0.15s', borderRadius:'var(--radius-lg) 0 0 0' }}
                onClick={() => setDropdownOpen(o => !o)}
              >
                <span style={{ fontSize:13, fontWeight:500, color:'var(--text-secondary)' }}>{type}</span>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
              </div>
              {dropdownOpen && (
                <div className="dropdown">
                  {['기업명','사업자번호','법인번호'].map(t => (
                    <div key={t} className="dropdown-item" onClick={() => { setType(t); setDropdownOpen(false); }}>{t}</div>
                  ))}
                </div>
              )}
            </div>

            <input
              style={{ flex:1, border:'none', outline:'none', padding:'13px 15px', fontFamily:'inherit', fontSize:13, color:'var(--text-primary)', background:'transparent' }}
              placeholder={type === '기업명' ? '기업명을 검색하세요' : `${type}를 입력하세요`}
              value={company}
              onChange={e => setCompany(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
            />

            <label
              style={{ display:'flex', alignItems:'center', gap:7, padding:'13px 18px', borderLeft:'1px solid var(--border)', cursor:'pointer', fontSize:13, fontWeight:500, color:'var(--text-secondary)', whiteSpace:'nowrap', transition:'all 0.15s', background:'transparent', borderTop:'none', borderBottom:'none', borderRight:'none', borderRadius:'0 var(--radius-lg) 0 0' }}
              htmlFor="fileInput"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>
              참조 문서 업로드
            </label>
            <input id="fileInput" type="file" style={{ display:'none' }} ref={fileRef} multiple onChange={e => setFile(e.target.files[0]?.name || null)} />
          </div>

          {file && (
            <div style={{ display:'flex', alignItems:'center', gap:6, margin:'8px 14px 0', padding:'5px 10px', background:'var(--teal-light)', borderRadius:6, fontSize:12, color:'var(--teal)', fontWeight:500 }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/></svg>
              <span>{file}</span>
              <span style={{ cursor:'pointer', opacity:.6, marginLeft:'auto' }} onClick={() => { setFile(null); fileRef.current.value=''; }}>✕</span>
            </div>
          )}

          <div style={{ position:'relative' }}>
            <textarea
              style={{ width:'100%', border:'none', outline:'none', resize:'none', padding:'14px 56px 14px 18px', fontFamily:'inherit', fontSize:13, color:'var(--text-primary)', background:'transparent', minHeight:84, lineHeight:1.6, borderRadius:'0 0 var(--radius-lg) var(--radius-lg)' }}
              placeholder="기업을 분석하는데 필요한 추가 정보가 있다면 알려주세요."
              rows={3}
              value={memo}
              onChange={e => setMemo(e.target.value)}
            />
            <button
              style={{ position:'absolute', right:14, bottom:14, width:36, height:36, background:'var(--teal)', border:'none', borderRadius:'50%', display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', boxShadow:'var(--shadow-btn)', transition:'all 0.2s' }}
              onClick={handleSend}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="white" style={{ marginLeft:2 }}><polygon points="5,3 19,12 5,21"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
