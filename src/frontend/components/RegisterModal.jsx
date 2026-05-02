import { useState } from 'react';
import { useApp } from '../context/AppContext';

export default function RegisterModal({ open, onClose }) {
  const { registerCompany } = useApp();
  const [form, setForm] = useState({ name: '', bizNo: '', dept: '', cycle: '' });

  if (!open) return null;

  const handleSubmit = () => {
    if (!form.name.trim()) return;
    registerCompany({
      name: form.name.trim(),
      bizNo: form.bizNo,
      dept: form.dept,
      cycle: form.cycle || '미설정',
      regDate: new Date().toISOString().slice(0, 10),
    });
    setForm({ name: '', bizNo: '', dept: '', cycle: '' });
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal">
        <button className="modal-close" onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
        <div className="modal-title">기업 등록</div>

        <div className="form-group">
          <label className="form-label">기업명</label>
          <input className="form-input" type="text" placeholder="기업명을 입력하세요"
            value={form.name} onChange={e => setForm(p => ({...p, name: e.target.value}))} />
        </div>
        <div className="form-group">
          <label className="form-label">사업자등록번호</label>
          <input className="form-input" type="text" placeholder="000-00-00000"
            value={form.bizNo} onChange={e => setForm(p => ({...p, bizNo: e.target.value}))} />
        </div>
        <div className="form-group">
          <label className="form-label">부서 선택</label>
          <div className="form-select-wrap">
            <select className="form-select form-input" value={form.dept}
              onChange={e => setForm(p => ({...p, dept: e.target.value}))}>
              <option value="">부서를 선택하세요</option>
              <option>기업금융부</option>
              <option>시너지금융부</option>
              <option>투자금융부</option>
            </select>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
        </div>
        <div className="form-group">
          <label className="form-label">배치 주기</label>
          <div className="cycle-options">
            {['매일','매주','매월','분기'].map(c => (
              <label key={c} className={`cycle-option${form.cycle === c ? ' selected' : ''}`}
                onClick={() => setForm(p => ({...p, cycle: c}))}>
                {c}
              </label>
            ))}
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-cancel" onClick={onClose}>취소</button>
          <button className="btn-submit" onClick={handleSubmit}>등록하기</button>
        </div>
      </div>
    </div>
  );
}
