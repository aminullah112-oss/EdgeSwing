import os
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
DATA_DIR=ROOT/'data'; DATA_DIR.mkdir(exist_ok=True)
DATABASE_PATH=os.getenv('DATABASE_PATH',str(DATA_DIR/'market.db'))
KITE_API_KEY=os.getenv('KITE_API_KEY','')
KITE_API_SECRET=os.getenv('KITE_API_SECRET','')
KITE_REDIRECT_URL=os.getenv('KITE_REDIRECT_URL','http://127.0.0.1:8000/auth/callback')
APP_ACCESS_KEY=os.getenv('APP_ACCESS_KEY','')
FRONTEND_URL=os.getenv('FRONTEND_URL','http://127.0.0.1:8000/')
ALLOWED_ORIGINS=[o.strip().rstrip('/') for o in os.getenv('ALLOWED_ORIGINS','http://127.0.0.1:8000,http://localhost:8000').split(',') if o.strip()]
