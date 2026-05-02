import { createContext, useContext, useState, useCallback } from 'react';

const AppContext = createContext(null);

export const PASTEL2 = [
  '#B3E2CD','#FDCDAC','#CBD5E8','#F4CAE4',
  '#E6F5C9','#FFF2AE','#F1E2CC','#CCCCCC'
];
export const randomPastel = () => PASTEL2[Math.floor(Math.random() * PASTEL2.length)];

export function AppProvider({ children }) {
  const [page, setPage] = useState('home');          // 현재 페이지
  const [activeNav, setActiveNav] = useState('yeosin');

  // 여신심사 보고서
  const [reportCompany, setReportCompany] = useState('');
  const [reportSaved, setReportSaved] = useState(false);

  // 사후관리
  const [mgmtCompanies, setMgmtCompanies] = useState([
    {
      id: 1, name: '삼성전자', dept: '기업금융부',
      regDate: '2026-04-01', cycle: '매월',
      color: '#CBD5E8',
      news: [
        { type: 'pos', title: '긍정적 실적 발표', desc: '사상 최대치 실적 발표로 기대 주가 상승', date: '2026-04-01', source: '뉴스' },
        { type: 'neg', title: '긍정적 실적 발표', desc: '삼성전자 파업, 수십조 피해 넘어 공급망 회복 불가 훼손', date: '2026-04-01', source: '뉴스' },
      ]
    }
  ]);
  const [detailCompany, setDetailCompany] = useState(null);

  // 보관함
  const [savedReports, setSavedReports] = useState([]);
  const [storageDetail, setStorageDetail] = useState(null);

  const navigate = useCallback((p, nav) => {
    setPage(p);
    if (nav) setActiveNav(nav);
  }, []);

  // 보고서 생성
  const createReport = useCallback((company) => {
    setReportCompany(company);
    setReportSaved(false);
    setPage('report');
    setActiveNav('yeosin');
  }, []);

  // 보고서 저장
  const saveReport = useCallback(() => {
    if (reportSaved) return;
    const today = new Date().toISOString().slice(0, 10);
    setSavedReports(prev => [...prev, {
      id: Date.now(),
      name: reportCompany,
      dept: '',
      rating: 'B+',
      ratingName: '양호',
      date: today,
      color: randomPastel(),
    }]);
    setReportSaved(true);
    return true;
  }, [reportCompany, reportSaved]);

  // 사후관리 기업 등록
  const registerCompany = useCallback((data) => {
    setMgmtCompanies(prev => [...prev, {
      id: Date.now(),
      color: randomPastel(),
      news: [],
      ...data,
    }]);
  }, []);

  // 보관함 삭제
  const deleteStoredReport = useCallback((id) => {
    setSavedReports(prev => prev.filter(r => r.id !== id));
  }, []);

  return (
    <AppContext.Provider value={{
      page, navigate, activeNav,
      reportCompany, reportSaved, createReport, saveReport,
      mgmtCompanies, registerCompany, detailCompany, setDetailCompany,
      savedReports, storageDetail, setStorageDetail, deleteStoredReport,
    }}>
      {children}
    </AppContext.Provider>
  );
}

export const useApp = () => useContext(AppContext);
