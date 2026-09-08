from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent
COURSE_CSV = BASE_DIR / 'data' / 'courses.csv'
ROUTES_GEOJSON = BASE_DIR / 'data' / 'routes.geojson'
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY','')
OPENAI_MODEL = os.getenv('OPENAI_MODEL','gpt-5')
KAKAO_REST_API_KEY = os.getenv('KAKAO_REST_API_KEY','')
SEOUL_API_KEY = os.getenv('SEOUL_API_KEY','')
KMA_API_KEY = os.getenv('KMA_API_KEY','')
EMBEDDING_MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
DATABASE_URL = os.getenv('DATABASE_URL','').strip()
