"""
RunMood DEM 고도 재계산 안내
현재 data/courses.csv에는 이미 DEM 계산 결과가 포함되어 있습니다.
DEM을 교체할 경우 data/dem 폴더의 GeoTIFF를 사용해 다시 계산하도록 확장할 수 있습니다.
현재 통합본의 계산 기준:
- 약 30m 간격 경로 샘플링
- 5포인트 중앙값 스무딩
- 2m 미만 고도변화는 누적상승/하강에서 제외
- Copernicus GLO-30 DSM 기반
"""
print("현재 courses.csv에는 DEM 계산 결과가 이미 반영되어 있습니다.")
