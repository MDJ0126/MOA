"""Request this project's app/supervisor shutdown without contacting a port."""
import argparse
from pathlib import Path
from app_lifecycle import request_stop,create_request

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    if not 1<=args.port<=65535:raise SystemExit('포트는 1~65535 범위여야 합니다.')
    supervisor_requested=False
    if args.port==8765:
        folder=ROOT/'data'
        if folder.is_dir() and folder.resolve()==ROOT/'data':create_request(folder/'supervisor.stop');supervisor_requested=True
    try:
        request_stop(ROOT,args.port)
        print('이 프로젝트 앱에 종료 요청을 저장했습니다. 진행 중 단계의 정리를 기다립니다.')
    except (OSError,ValueError):
        if not supervisor_requested:raise SystemExit('이 프로젝트의 실행 기록이 없습니다. 다른 포트의 앱에 종료 요청을 보내지 않았습니다.')
        print('이 프로젝트 감독에 종료 요청을 저장했습니다. 앱 종료 여부는 아직 미확인입니다.')

if __name__=='__main__':main()
