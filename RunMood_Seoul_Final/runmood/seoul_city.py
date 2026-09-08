import requests
import urllib.parse
import xml.etree.ElementTree as ET
from .config import SEOUL_API_KEY

def get_citydata_xml(area_name: str):
    if not SEOUL_API_KEY:
        return None
    area = urllib.parse.quote(area_name, safe="")
    url = f"http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/xml/citydata/1/5/{area}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.text

def get_congestion(area_name: str):
    """
    서울 실시간 도시데이터의 XML 응답에서 혼잡 관련 값을 가능한 범위에서 추출.
    장소가 121개 실시간 대상에 포함되지 않거나 API키가 없으면 None 반환.
    """
    try:
        xml_text = get_citydata_xml(area_name)
        if not xml_text:
            return None
        root = ET.fromstring(xml_text)
        def txt(tag):
            node = root.find(f".//{tag}")
            return node.text if node is not None else None
        return {
            "area_name": txt("AREA_NM"),
            "level": txt("AREA_CONGEST_LVL"),
            "message": txt("AREA_CONGEST_MSG"),
            "min_people": txt("AREA_PPLTN_MIN"),
            "max_people": txt("AREA_PPLTN_MAX"),
        }
    except Exception as e:
        print(f"[Seoul citydata warning] {e}")
        return None
