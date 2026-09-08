"""
서울 열린데이터광장에서 내려받은 파일을 RunMood 형식으로 정리하기 위한 보조 스크립트.

권장 원본:
- 서울시 둘레길 선형 위치정보(WGS1984)
- 서울시 주요 공원현황
- 서울시 공중화장실 위치정보

주의:
공공데이터 파일의 컬럼명/형식은 버전에 따라 달라질 수 있으므로
이 스크립트는 공원 XLSX의 핵심 필드를 자동 탐색하는 예제다.
실제 러닝 '코스'는 선형 경로/거리/난이도 검증 후 courses.csv에 넣는 것을 권장한다.
"""

from pathlib import Path
import pandas as pd

DATA = Path("data")

def pick(df, names):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in lookup:
            return lookup[n.lower()]
    return None

def preview_parks(filename="seoul_parks.xlsx"):
    path = DATA / filename
    df = pd.read_excel(path)

    c_name = pick(df, ["공원명","PARK_NM"])
    c_addr = pick(df, ["주소","PARK_ADDR","도로명주소"])
    c_lat = pick(df, ["위도","LAT","LATITUDE","YCRD"])
    c_lon = pick(df, ["경도","LON","LNG","LONGITUDE","XCRD"])
    c_desc = pick(df, ["공원개요","PARK_OTLN","설명"])
    c_fac = pick(df, ["주요시설","MAIN_FCLT","시설"])

    print("감지된 컬럼:")
    print({
        "name": c_name, "address": c_addr, "lat": c_lat,
        "lon": c_lon, "description": c_desc, "facilities": c_fac
    })

    cols = [c for c in [c_name,c_addr,c_lat,c_lon,c_desc,c_fac] if c]
    print(df[cols].head(20).to_string(index=False))

if __name__ == "__main__":
    preview_parks()
