from runmood.weather import get_current_weather, running_weather_message

# 서울시청 근처 좌표로 API 연결 테스트
LAT = 37.5665
LON = 126.9780

weather = get_current_weather(LAT, LON)

print("=== 기상청 실시간 날씨 테스트 ===")
print(weather)

if weather.get("ok"):
    print()
    print("기온:", weather.get("temperature_c"), "℃")
    print("습도:", weather.get("humidity_pct"), "%")
    print("풍속:", weather.get("wind_speed_mps"), "m/s")
    print("1시간 강수량:", weather.get("rain_1h_mm"), "mm")
    print("강수형태:", weather.get("precipitation_type"))
    print("러닝 안내:", running_weather_message(weather))
else:
    print("오류:", weather.get("error"))
