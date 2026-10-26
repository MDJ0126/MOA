"""Interruptible local inference for optional background assessments."""
import http.client,io,json,socket,threading,time,urllib.error
from model_transport import MAX_RESPONSE,ModelError,http_error,request_json,validate_url

class BackgroundYield(RuntimeError):pass

def request_background_json(request,timeout,should_yield):
    target=validate_url(request.full_url)
    if target.scheme!='http' or target.hostname not in ('localhost','127.0.0.1','::1'):
        return request_json(request,timeout)
    connection=http.client.HTTPConnection(target.hostname,target.port or 80,timeout=timeout)
    done=threading.Event();cancelled=threading.Event();result=[]
    def disconnect():
        cancelled.set()
        channel=connection.sock
        if channel:
            try:channel.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        connection.close()
    def work():
        try:
            if cancelled.is_set():return
            path=target.path or '/'
            if target.query:path+='?'+target.query
            connection.request(request.get_method(),path,body=request.data,headers=dict(request.header_items()))
            response=connection.getresponse();raw=response.read(MAX_RESPONSE+1)
            if response.status>=400:
                raise http_error(urllib.error.HTTPError(request.full_url,response.status,response.reason,response.headers,io.BytesIO(raw)))
            if len(raw)>MAX_RESPONSE:raise ModelError('response_too_large','모델 응답 크기가 한도를 넘었습니다.')
            try:value=json.loads(raw)
            except (ValueError,UnicodeError):raise ModelError('invalid_response','모델 응답을 JSON으로 읽지 못했습니다.') from None
            if not isinstance(value,dict) or value.get('error'):raise ModelError('invalid_response','모델 서버가 정상 결과 객체를 반환하지 않았습니다.')
            result.append(value)
        except ModelError as error:result.append(error)
        except (OSError,http.client.HTTPException):result.append(ModelError('connection','백그라운드 모델 연결이 종료되었거나 시간이 초과되었습니다.',retryable=True))
        finally:
            connection.close();done.set()
    thread=threading.Thread(target=work,daemon=True);thread.start();deadline=time.monotonic()+timeout
    try:
        while not done.wait(.1):
            if should_yield():
                disconnect();thread.join(1)
                raise BackgroundYield('사용자 작업을 우선하여 평가를 대기열로 돌렸습니다.')
            if time.monotonic()>=deadline:
                disconnect();thread.join(1)
                raise ModelError('connection','백그라운드 모델 응답 시간이 초과되었습니다.',retryable=True)
        if not result:raise BackgroundYield('중단된 평가를 대기열로 돌렸습니다.')
        if isinstance(result[0],Exception):raise result[0]
        return result[0]
    finally:
        if not done.is_set():disconnect()
