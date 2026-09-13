"""Convenience launcher: `python run.py` == `uvicorn app.main:app --reload`."""
import argparse
import os
from pathlib import Path
import uvicorn

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Run Orbital Guardian locally")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--reload",action="store_true")
    args=parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    uvicorn.run("app.main:app",host=args.host,port=args.port,reload=args.reload)
