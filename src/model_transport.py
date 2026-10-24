"""Bounded model HTTP responses, safe diagnostics, and retry classification."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import http.client
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

MAX_RESPONSE=8*1024**2
RETRY_STATUSES={408,425,429,500,502,503,504}


class ModelError(RuntimeError):
    def __init__(self,code,message,*,status=None,retryable=False,retry_after=None):
        super().__init__(message)
        self.code=code;self.status=status;self.retryable=retryable;self.retry_after=retry_after
    def details(self):
        return {'code':self.code,'status':self.status,'retryable':self.retryable,'retry_after':self.retry_after,'message':str(self)}


def validate_url(url):
    try:
        target=urlparse(url)
        if target.scheme not in ('http','https') or not target.hostname or target.username is not None or target.password is not None:raise ValueError()
        if target.port is not None and not 1<=target.port<=65535:raise ValueError()
        return target
    except (TypeError,ValueError,AttributeError):
        raise ModelError('invalid_address','모델 서버 주소를 확인하세요. 인증정보 없는 HTTP 또는 HTTPS 주소와 올바른 포트를 사용하세요.') from None


def retry_delay(value):
    if value is None:return None
    try:seconds=float(value)
    except (TypeError,ValueError):
        try:seconds=(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
        except (TypeError,ValueError,OverflowError):return None
    if not 0<=seconds<float('inf'):return None
    return min(300,max(0,seconds))


def http_error(error):
    try:body=error.read(16384).decode('utf-8',errors='replace').lower()
    except (OSError,ValueError,AttributeError):body=''
    context=any(marker in body for marker in ('context window','context size','context length','n_ctx')) and any(marker in body for marker in ('exceed','too long','too many','larger than','greater than','overflow'))
    status=error.code;retryable=status in RETRY_STATUSES
    if status in (400,413,422) and context:
        code,message='context_limit','모델 입력 길이 한도를 넘었습니다. 작업 내용·추가 지시를 나누거나 컨텍스트 설정을 확인하세요.'
    elif status in (401,403):code,message='authentication','모델 서버의 인증·접근 설정을 확인한 뒤 다시 시도하세요.'
    elif status==404:code,message='model_not_found','설정한 모델 이름과 모델 서버 주소를 확인하세요.'
    elif status in (400,413,422):code,message='invalid_request','모델 서버가 요청을 거절했습니다. 입력 길이·모델 이름·지원 형식을 확인하세요.'
    elif retryable:code,message='server_unavailable','모델 서버가 일시적으로 요청을 처리하지 못했습니다.'
    else:code,message='http_error','모델 서버가 요청을 처리하지 못했습니다. 서버 설정과 상태를 확인하세요.'
    delay=retry_delay(error.headers.get('Retry-After')) if error.headers and retryable else None
    return ModelError(code,f'{message} (HTTP {status})',status=status,retryable=retryable,retry_after=delay)


def request_json(request,timeout):
    validate_url(request.full_url)
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:raise ModelError('response_too_large','모델 응답 크기가 한도를 넘었습니다. 작업을 작은 단위로 나누세요.')
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise ModelError('invalid_response','모델 응답을 JSON으로 읽지 못했습니다. 모델 서버의 응답 형식을 확인하세요.') from None
        if not isinstance(value,dict) or value.get('error'):raise ModelError('invalid_response','모델 서버가 정상 결과 객체를 반환하지 않았습니다. 모델 서버 상태를 확인하세요.')
        return value
    except urllib.error.HTTPError as error:
        try:classified=http_error(error)
        finally:error.close()
        raise classified from None
    except urllib.error.URLError as error:
        if isinstance(error.reason,ssl.SSLCertVerificationError):
            raise ModelError('certificate','모델 서버 인증서를 확인하지 못했습니다. 인증서와 서버 주소를 확인하세요.') from None
        raise ModelError('connection','모델 서버에 연결하지 못했습니다. 서버 실행·주소를 확인하세요.',retryable=True) from None
    except (TimeoutError,ConnectionError,http.client.HTTPException):
        raise ModelError('connection','모델 서버 응답이 끊겼거나 시간이 초과되었습니다.',retryable=True) from None


def completion(value):
    try:
        choice=value['choices'][0];content=choice['message']['content']
        if not isinstance(content,str) or not content.strip():raise ValueError()
        return content,choice.get('finish_reason')
    except (KeyError,IndexError,TypeError,ValueError):
        raise ModelError('invalid_response','모델 응답의 결과 텍스트가 없거나 형식이 올바르지 않습니다.') from None


def ollama_text(value):
    try:
        content=value['message']['content']
        if not isinstance(content,str) or not content.strip():raise ValueError()
        return content
    except (KeyError,TypeError,ValueError):
        raise ModelError('invalid_response','Ollama 응답의 결과 텍스트가 없거나 형식이 올바르지 않습니다.') from None


def model_output(raw):
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,TypeError):
        raise ModelError('invalid_model_output','모델이 요청한 결과 형식을 지키지 않았습니다. 작업 내용을 구체화한 뒤 다시 시도하세요.') from None
