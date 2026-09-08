import re, json
from .models import UserIntent
from .config import OPENAI_API_KEY, OPENAI_MODEL

def rule_parse(q):
    districts=['강남구','강동구','강북구','강서구','관악구','광진구','구로구','금천구','노원구','도봉구','동대문구','동작구','마포구','서대문구','서초구','성동구','성북구','송파구','양천구','영등포구','용산구','은평구','종로구','중구','중랑구']
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:km|킬로|키로)', q.lower())
    dist=float(m.group(1)) if m else None
    mood=[]; env=[]; terrain=[]
    for tag,words in {
        '힐링':['힐링','스트레스','편하게','기분전환'],
        '조용함':['조용','한적','사람 적','사람 없는'],
        '야경':['야경','밤','저녁','퇴근'],
        '자연':['자연','숲','나무','녹지'],
        '개방감':['시원','탁 트','개방감']}.items():
        if any(w in q for w in words): mood.append(tag)
    if any(w in q for w in ['강','한강','하천','물가','천변']): env.append('하천')
    if any(w in q for w in ['공원','숲','녹지','자연']): env.append('공원')
    if any(w in q for w in ['평지','평평']): terrain.append('평지')
    if any(w in q for w in ['산','오르막','언덕','트레일']): terrain.append('산림')
    difficulty='쉬움' if any(w in q for w in ['초보','쉬운','편하게','가볍게']) else None
    if any(w in q for w in ['힘들','고강도','도전','빡세']): difficulty='어려움'
    return UserIntent(
        location=next((d for d in districts if d in q),'서울'), distance_km=dist,
        difficulty=difficulty, terrain=terrain, environment=env, mood=mood,
        wants_quiet=any(w in q for w in ['조용','한적','사람 적','사람 없는']),
        wants_night_view=any(w in q for w in ['야경','밤','저녁']), wants_facilities=True)

def openai_parse(q):
    from openai import OpenAI
    client=OpenAI(api_key=OPENAI_API_KEY)
    prompt='''서울 러닝 추천 의도 분석기다. 다음 JSON만 반환한다: {"location":"서울 또는 자치구","distance_km":숫자또는null,"difficulty":"쉬움/보통/어려움/null","terrain":[],"environment":[],"mood":[],"wants_quiet":false,"wants_night_view":false,"wants_facilities":true}. 과도하게 추측하지 마라.'''
    r=client.responses.create(model=OPENAI_MODEL,input=[{'role':'system','content':prompt},{'role':'user','content':q}])
    text=r.output_text.strip()
    if text.startswith('```'):
        text=text.strip('`').replace('json\n','',1)
    return UserIntent.model_validate(json.loads(text))

def parse_intent(q):
    if OPENAI_API_KEY:
        try: return openai_parse(q), 'openai'
        except Exception as e: print('[OpenAI fallback]',e)
    return rule_parse(q), 'rule'
