from fastapi import FastAPI, HTTPException
from runmood.models import RecommendRequest
from runmood.recommender import recommend
from runmood.vector_store import build

app=FastAPI(title="RunMood Seoul API",description="서울 실제 공간·보행·노면·대기 데이터 기반 러닝 코스 추천",version="2.0.0")

@app.get("/")
def root():
    return {"service":"RunMood Seoul","docs":"/docs","health":"/health","recommend":"/recommend"}

@app.get("/health")
def health(): return {"ok":True}

@app.post("/admin/rebuild-db")
def rebuild_db(): return {"ok":True,"count":build(True)}

@app.post("/recommend")
def recommend_api(req: RecommendRequest):
    try:
        intent,items,parser=recommend(req.query,req.top_k)
        return {"query":req.query,"intent":intent.model_dump(),"parser":parser,"recommendations":items}
    except Exception as e:
        raise HTTPException(status_code=500,detail=str(e))
