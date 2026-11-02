import argparse
import os
from pathlib import Path
import uvicorn
from .web import create_app


def main():
    parser=argparse.ArgumentParser(description="PostScope 로컬 게시 기록 분석")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--data-dir",default=os.environ.get("POSTSCOPE_DATA_DIR",str(Path.cwd()/"data")))
    args=parser.parse_args()
    print(f"PostScope: http://127.0.0.1:{args.port} (이 PC에서만 접근 가능)")
    uvicorn.run(create_app(args.data_dir),host="127.0.0.1",port=args.port,access_log=False)


if __name__=="__main__":
    main()
