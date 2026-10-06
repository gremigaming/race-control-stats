# Phases 1-2 of the real server rollout (approved by GreMi 2026-10-06): rename/move channels in place,
# archive 7 channels, create 6 new ones. Idempotent; no deletes; no role changes. Use --dry first.
# Auth: relies on the cloud environment proxy adding the bot token for discord.com.
import json,subprocess,time,sys
G='1079917337165172876'; API='https://discord.com/api/v10'
DRY='--dry' in sys.argv
def bold(s):
    o=''
    for ch in s:
        if 'A'<=ch<='Z': o+=chr(0x1D5D4+ord(ch)-65)
        elif 'a'<=ch<='z': o+=chr(0x1D5D4+ord(ch.upper())-65)
        elif '0'<=ch<='9': o+=chr(0x1D7EC+ord(ch)-48)
        else: o+=ch
    return o
def C(e,n): return f'{e}┃{bold(n)}'
def CAT(e,n): return f'『 {e} {bold(n)} 』'
def req(method,path,body=None):
    for i in range(8):
        cmd=['curl','-sS','-X',method,'-w','\n%{http_code}',API+path]
        if body is not None: cmd+=['-H','Content-Type: application/json','--data-binary',json.dumps(body)]
        r=subprocess.run(cmd,capture_output=True,text=True).stdout
        b,c=r.rsplit('\n',1)
        if c in('200','201','204'): return json.loads(b) if b.strip() else None
        if c=='429':
            ra=json.loads(b).get('retry_after',5); print(f'  rate limited, waiting {ra}s',flush=True); time.sleep(ra+0.5); continue
        if c.startswith('5'): time.sleep(2**i); continue
        raise RuntimeError(f'{method} {path} -> {c} {b[:300]}')
    raise RuntimeError('retries exhausted')

PROTECTED={'1079917581386924052','1079917581386924053'}
# category id -> (new name, order)
CATS=[('1085834199145127986',CAT('📌','START HERE')),
 ('1079917340298321940',CAT('🏁','COMMUNITY HUB')),
 ('1080048525657772083',CAT('📺','CONTENT & STREAMS')),
 ('1508742900647723008',CAT('🏎️','RACING')),
 ('1079917340298321941',CAT('🎙️','VOICE CHANNELS')),
 ('1085852657001967696',CAT('🛠️','SUPPORT')),
 ('1079923891897630802',CAT('🔒','STAFF ONLY')),
 ('1508740231896105010',None),('1508741121411649616',None),
 ('1099006172922654780',CAT('🗄️','ARCHIVE'))]
# per category: list of (channel id or None for new, emoji, name, type for new)
LAYOUT={
'1085834199145127986':[('1079917581386924052','📜','RULES'),('1080049074054635530','👋','START-HERE'),('1083312808759926844','🔗','SOCIALS'),(None,'👤','INTRODUCTIONS')],
'1079917340298321940':[('1079923632744173630','📢','ANNOUNCEMENTS'),('1079917340298321942','💬','GENERAL'),(None,'💡','FEEDBACK-AND-SUGGESTIONS'),('1085851731637846086','⭐','SUBSCRIBER-CHAT'),('1096181494231347240','🎬','CLIPS-AND-HIGHLIGHTS'),('1082967543142162522','🎁','GIVEAWAYS')],
'1080048525657772083':[('1105048860461645907','🗓️','STREAM-SCHEDULE'),('1079924693890510962','📣','SELF-PROMOTION'),('1080505995467431986','🔴','NEW-CONTENT'),('1080506154746126407','💬','STREAMER-CHAT')],
'1508742900647723008':[('1477423394118041630','🛠️','SETUPS'),('1532005211235684362','🏆','COMMUNITY-LEAGUES'),(None,'🏎️','F1-CHAT'),(None,'⏱️','LMU-CHAT'),('1080049792924778566','📰','F1-NEWS')],
'1079917340298321941':[('1079917340298321943','🎙️','PADDOCK'),('1079921783119028284','🏁','RACE-ROOM-1'),('1079922201668636752','🏁','RACE-ROOM-2'),('1096155578037981214','📺','STREAM-WATCH-PARTY'),('1096020822071717928','💤','AFK')],
'1085852657001967696':[(None,'🆘','SERVER-SUPPORT'),('1085852731887058944','🤖','BOT-COMMANDS')],
'1079923891897630802':[('1080048914507513856','🛡️','MOD-CHAT'),(None,'🚩','REPORTS'),('1091494080741122148','📋','LOGS'),('1113717920321773579','🤖','STAFF-COMMANDS'),('1079917581386924053','📢','DISCORD-UPDATES')],
}
ARCHIVE_CAT='1099006172922654780'
ARCHIVE=['1093081169228218418','1527572546080342096','1487367504035844146','1085834727086358588','1085852763335954534','1456427604969259030','1486093001859272855']

log=[]
def L(kind,what,before,after,result): log.append((kind,what,before,after,result)); print(kind,'|',what,'|',before,'->',after,'|',result,flush=True)
def chans(): return {c['id']:c for c in req('GET',f'/guilds/{G}/channels')}

cur=chans()
# 1. categories rename
for cid,new in CATS:
    if not new: continue
    c=cur[cid]
    if c['name']==new: L('category',cid,c['name'],new,'already done'); continue
    if DRY: L('category',cid,c['name'],new,'dry'); continue
    req('PATCH',f'/channels/{cid}',{'name':new}); time.sleep(1)
    got=req('GET',f'/channels/{cid}')['name']
    L('category',cid,c['name'],new,'ok' if got==new else f'MISMATCH got {got}')
# 2. channels rename+move
for cat,items in LAYOUT.items():
    for cid,e,n in items:
        if cid is None: continue
        c=cur[cid]; new=C(e,n)
        body={}
        if c['name']!=new: body['name']=new
        if c.get('parent_id')!=cat: body['parent_id']=cat
        if not body: L('channel',cid,c['name'],new,'already done'); continue
        if DRY: L('channel',cid,c['name'],new,f'dry {list(body)}'); continue
        req('PATCH',f'/channels/{cid}',body); time.sleep(1)
        g=req('GET',f'/channels/{cid}')
        ok=g['name']==new and g.get('parent_id')==cat and g.get('permission_overwrites')==c.get('permission_overwrites')
        L('channel',cid,f"{c['name']} [{cur.get(c.get('parent_id'),{}).get('name','none')}]",new,'ok, permissions unchanged' if ok else f"CHECK name={g['name']} parent={g.get('parent_id')} ow_same={g.get('permission_overwrites')==c.get('permission_overwrites')}")
# 3. archive moves (sync to hidden archive permissions)
for cid in ARCHIVE:
    c=cur[cid]
    if c.get('parent_id')==ARCHIVE_CAT and c.get('permission_overwrites')==cur[ARCHIVE_CAT]['permission_overwrites']: L('archive',cid,c['name'],'Archive','already done'); continue
    if cid in PROTECTED: L('archive',cid,c['name'],'-','SKIPPED protected'); continue
    if DRY: L('archive',cid,c['name'],'Archive','dry'); continue
    req('PATCH',f'/channels/{cid}',{'parent_id':ARCHIVE_CAT,'lock_permissions':True}); time.sleep(1)
    g=req('GET',f'/channels/{cid}')
    def norm(ow): return sorted((o['id'],str(o['allow']),str(o['deny'])) for o in ow)
    ok=g.get('parent_id')==ARCHIVE_CAT and norm(g['permission_overwrites'])==norm(cur[ARCHIVE_CAT]['permission_overwrites'])
    L('archive',cid,f"{c['name']} [{cur.get(c.get('parent_id'),{}).get('name','none')}]",'Archive (hidden)','ok' if ok else 'CHECK')
# 4. create missing
cur=chans()
for cat,items in LAYOUT.items():
    for cid,e,n in items:
        if cid is not None: continue
        new=C(e,n)
        if any(x['name']==new for x in cur.values()): L('create',new,'-',new,'already exists, skipped'); continue
        if DRY: L('create',new,'-',new,'dry'); continue
        ow=[{k:o[k] for k in ('id','type','allow','deny')} for o in cur[cat]['permission_overwrites']]
        g=req('POST',f'/guilds/{G}/channels',{'name':new,'type':0,'parent_id':cat,'permission_overwrites':ow}); time.sleep(1)
        g=req('GET',f"/channels/{g['id']}")
        def norm(ow): return sorted((o['id'],str(o['allow']),str(o['deny'])) for o in ow)
        synced=norm(g['permission_overwrites'])==norm(cur[cat]['permission_overwrites'])
        L('create',g['id'],'-',f"{g['name']} in {cur[cat]['name'] if False else cat}",'ok, '+('synced with category' if synced else 'NOT synced: '+json.dumps(g['permission_overwrites'])))
# 5. positions
if not DRY:
    cur=chans()
    order=[cid for cid,_ in CATS]
    req('PATCH',f'/guilds/{G}/channels',[{'id':cid,'position':i} for i,cid in enumerate(order)]); time.sleep(1)
    for cat,items in LAYOUT.items():
        ids=[]
        for cid,e,n in items:
            if cid is None: cid=next(x['id'] for x in cur.values() if x['name']==C(e,n))
            ids.append(cid)
        req('PATCH',f'/guilds/{G}/channels',[{'id':cid,'position':i} for i,cid in enumerate(ids)]); time.sleep(1)
    L('order','all','-','-','positions set')
json.dump(log,open('/mnt/project-files/discord/real-server-apply-log-2026-10-06.json','w'),ensure_ascii=False,indent=1)
