# 인천 CleanSYS TMS 자동 수집 및 공용 대시보드

PC를 꺼두어도 GitHub Actions가 매 30분마다 한국환경공단 CleanSYS 실시간 API에서 인천 6개 핵심 사업장(19개 배출구)의 질소산화물(NOx) 실측 농도를 자동 수집해 `data/YYYY-MM-DD.json`에 누적하고, GitHub Pages 링크로 접속한 모든 사람이 동일한 실시간·과거 기록을 열람할 수 있습니다.

## 폴더 구조
- `.github/workflows/collect-cleansys.yml`: 매 30분(:12, :42) 자동 실행 워크플로우
- `scripts/fetch_cleansys.py`: CleanSYS API 수집 및 일별 48슬롯(`00:00~23:30`) 누적 스크립트
- `data/index.json` 및 `data/YYYY-MM-DD.json`: 자동 누적되는 일별 실측 기록
- `index.html`: 화이트 테마 공용 대시보드 (API 키 미포함)

## 빠른 배포 순서
1. GitHub에서 새 **Public** 저장소 생성 후 이 파일들을 모두 업로드합니다.
2. **Settings → Secrets and variables → Actions → New repository secret**에서 이름 `CLEANSYS_API_KEY`, 값에 공공데이터포털 인증키를 저장합니다.
3. **Settings → Actions → General → Workflow permissions**에서 **Read and write permissions**를 선택하고 저장합니다.
4. **Settings → Pages**에서 Branch를 `main` / `/ (root)`로 선택해 저장합니다.
5. **Actions → CleanSYS 30분 자동 실측 수집 → Run workflow**를 1회 실행해 첫 실측 데이터를 수집합니다.
