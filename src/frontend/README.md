# 심사숙고 프론트엔드 — React 컴포넌트

## 📁 폴더 구조

```
src/frontend/
├── App.jsx                     # 루트 컴포넌트 + 라우터
├── context/
│   └── AppContext.jsx           # 전역 상태 관리 (페이지, 보고서, 보관함)
├── components/
│   ├── Sidebar.jsx              # 사이드바 네비게이션
│   ├── RegisterModal.jsx        # 기업 등록 모달
│   └── SearchRow.jsx            # 검색 입력 컴포넌트
├── pages/
│   ├── HomePage.jsx             # 홈 (여신심사 검색)
│   ├── ReportPage.jsx           # 여신심사 보고서
│   ├── ManagementPage.jsx       # 사후관리 목록
│   ├── ManagementDetailPage.jsx # 사후관리 상세 (뉴스/소송)
│   ├── StoragePage.jsx          # 보관함 목록
│   └── StorageDetailPage.jsx    # 보관함 상세 (저장된 보고서)
├── styles/
│   ├── global.css               # 전역 CSS 변수 및 공통 스타일
│   └── modal.css                # 모달/폼 스타일
└── assets/
    └── dandi.png                # 캐릭터 이미지
```

## 🚀 적용 방법

### 1. 파일 복사
```bash
# 기존 프로젝트의 src 폴더에 덮어쓰기
cp -r src/* your-project/src/
```

### 2. 의존성 확인
추가 패키지 없이 **React 18+** 만 있으면 됩니다.

### 3. main.jsx / index.jsx 설정
```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

### 4. 실행
```bash
npm run dev   # Vite
# or
npm start     # CRA
```

## 🎨 디자인 토큰 (CSS Variables)

| 변수 | 값 | 용도 |
|------|-----|------|
| `--teal` | `#00C4A9` | 주요 강조색 |
| `--teal-light` | `#E6FAF7` | 배경 강조 |
| `--main-bg` | `#F9F9F9` | 페이지 배경 |
| `--border` | `#E5E7EB` | 구분선 |
| `--radius-lg` | `16px` | 카드 반경 |

## 📄 페이지별 기능

| 페이지 | 경로(내부) | 주요 기능 |
|--------|-----------|----------|
| 홈 | `home` | 기업명/사업자번호 검색, 파일 업로드 |
| 보고서 | `report` | 요약/재무/위험/전체 탭, 보관함 저장 |
| 사후관리 | `mgmt` | 기업 등록, 카드 그리드, 부서 필터 |
| 사후관리 상세 | `mgmt-detail` | 최근 뉴스 / 소송내역 |
| 보관함 | `storage` | 저장된 보고서 카드, 검색/필터 |
| 보관함 상세 | `storage-detail` | 보고서 뷰, 삭제 |
