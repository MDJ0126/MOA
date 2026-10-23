"""Message routing and legacy-record compatibility for frozen task teams."""
import json
import re


def recipient_of(message):
    if message.get('recipient'):return message['recipient']
    try:
        value=json.loads(message.get('content',''))
        if isinstance(value,dict):return value.get('recipient','')
    except (ValueError,TypeError):pass
    content=message.get('content')
    match=re.match(r'^[^ ]+ → ([^:]+): ',content) if message.get('kind')=='message' and isinstance(content,str) else None
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


def history_plan(raw):
    from setup_config import legacy_plan,validate_team
    if raw is None or raw=='':return legacy_plan()
    try:
        plan=json.loads(raw)
        if not isinstance(plan,dict):return None
        clean=validate_team({'agents':plan.get('agents'),'pipeline':plan.get('pipeline')})
        assignment=plan.get('assignment')
        if not isinstance(assignment,dict):return None
        agents={a['id']:a for a in clean['agents']}
        for role in clean['pipeline']:
            actor=assignment.get(role)
            if not isinstance(actor,str) or actor not in agents or role not in agents[actor]['capabilities']:return None
        return plan
    except (ValueError,TypeError,UnicodeError,OverflowError):return None


def task_messages(connection,tid,plan,agent='',limit=50001,before=None,descending=False):
    query='SELECT * FROM messages WHERE task_id=?';args=[tid]
    if before is not None:query+=' AND seq<?';args.append(before)
    query+=' ORDER BY seq'+(' DESC' if descending else '')
    result=[]
    for row in connection.execute(query,args):
        message=enrich(row,plan) if plan is not None else dict(row)
        matches=for_agent(message,agent,plan) if plan is not None else (message['agent']==agent or recipient_of(message)==agent or message['agent']=='user' and message['kind']=='instruction' and not recipient_of(message))
        if not agent or matches:
            result.append(message)
            if len(result)>=limit:break
    return result
