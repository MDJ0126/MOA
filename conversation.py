"""Message routing and legacy-record compatibility for frozen task teams."""
import json
import re


def recipient_of(message):
    if message.get('recipient'):return message['recipient']
    try:
        value=json.loads(message.get('content',''))
        if isinstance(value,dict):return value.get('recipient','')
    except (ValueError,TypeError):pass
    match=re.match(r'^[^ ]+ → ([^:]+): ',message.get('content','')) if message.get('kind')=='message' else None
    return match[1] if match else ''


def for_agent(message,agent,plan):
    ids={a['id'] for a in plan['agents']}
    if agent not in ids and agent in plan['assignment']:
        if message.get('capability')==agent or message['agent']==agent:return True
        recipient=recipient_of(message)
        return message['agent']=='user' and (recipient in (agent,plan['assignment'][agent]) or not recipient and message['kind']=='instruction')
    if message['agent']==agent:return True
    recipient=recipient_of(message)
    if recipient==agent:return True
    if recipient and recipient not in ids and plan['assignment'].get(recipient)==agent:return True
    return agent in ids and not recipient and message['agent']=='user' and message['kind']=='instruction'


def enrich(message,plan):
    value=dict(message);recipient=recipient_of(value)
    ids={a['id'] for a in plan['agents']}
    value['recipient']=recipient if recipient in ids else plan['assignment'].get(recipient,recipient)
    return value


def task_messages(connection,tid,plan,agent='',limit=50001,before=None,descending=False):
    query='SELECT * FROM messages WHERE task_id=?';args=[tid]
    if before is not None:query+=' AND seq<?';args.append(before)
    query+=' ORDER BY seq'+(' DESC' if descending else '')
    result=[]
    for row in connection.execute(query,args):
        message=enrich(row,plan)
        if not agent or for_agent(message,agent,plan):
            result.append(message)
            if len(result)>=limit:break
    return result
