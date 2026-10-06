# Verification plan plus the server review fixes Milan approved on 2026-10-06 ("all except 1, 5, 10, 13").
# Stages run in order and are idempotent: roles, members, channels, extras, onboarding.
# Usage: python3 discord/apply_real_server_verification.py <stage> [--dry]
# Backup taken first: /mnt/project-files/discord/real-server-backup-before-verification-0937.json
# Auth: relies on the cloud environment proxy adding the bot token for discord.com.
import json,subprocess,sys,time
G='1079917337165172876'; API='https://discord.com/api/v10'
DRY='--dry' in sys.argv
STAGE=sys.argv[1] if len(sys.argv)>1 else ''

def req(method,path,body=None,reason=None):
    for i in range(8):
        cmd=['curl','-sS','-X',method,'-w','\n%{http_code}',API+path]
        if body is not None: cmd+=['-H','Content-Type: application/json','--data-binary',json.dumps(body)]
        b,c=subprocess.run(cmd,capture_output=True,text=True).stdout.rsplit('\n',1)
        if c in('200','201','204'): return json.loads(b) if b.strip() else None
        if c=='429': time.sleep(json.loads(b).get('retry_after',2)+0.3); continue
        if c.startswith('5'): time.sleep(2**i); continue
        raise RuntimeError(f'{method} {path} -> {c} {b[:300]}')
    raise RuntimeError('retries exhausted')
def write(method,path,body=None):
    if DRY: print('   dry',method,path); return None
    r=req(method,path,body); time.sleep(0.4); return r

# ---------- ids ----------
EVERYONE=G; MEMBER='1097214965640855764'; MOD='1080524470332174437'
F1='1508728733111161023'; LMU='1508728759182692422'; LEAGUE='1556952638413742180'
LIVE='1080549508481564672'; YOUTUBE='1117766544806641695'; TIKTOK='1117766587831824384'
BOT_ROLES=['1080548440787919039','1118232009718898791','1082967324954480694','1456382464774639626',
           '1508481840909127764','1091495992651694154','1493589040887238709']  # Streamcord, Streamcord Pro, Giveaway Boat, SignupBot, Medal, Pingcord, TikTok bot
NEW_ROLES=['Pit Wall','Prize Hunter','Stats Nerd']
VIEW=1024; SEND=2048; CONNECT=1<<20
TALK=412317191232  # same allow set the F1 role already had in its chats (view, send, threads, reactions, history...)
BOTSET=VIEW|SEND|16384|32768|65536  # view, send, embed, attach, read history
STATS_CAT='1556945370959843380'
CH=dict(RULES='1079917581386924052',START='1080049074054635530',SOCIALS='1083312808759926844',INTRO='1556941692139995157',
 ANN='1079923632744173630',GENERAL='1079917340298321942',FEEDBACK='1556941698490306621',CLIPS='1096181494231347240',
 GIVEAWAYS='1082967543142162522',SUBCHAT='1085851731637846086',SCHEDULE='1105048860461645907',PROMO='1079924693890510962',
 NEWCONTENT='1080505995467431986',STREAMERCHAT='1080506154746126407',SETUPS='1477423394118041630',LEAGUES='1532005211235684362',
 F1CHAT='1556941705322692608',LMUCHAT='1556941712130310244',F1NEWS='1080049792924778566',PADDOCK='1079917340298321943',
 RR1='1079921783119028284',RR2='1079922201668636752',WATCH='1096155578037981214',AFK='1096020822071717928',
 SUPPORT='1556941718874751006',BOTCMD='1085852731887058944',MODCHAT='1080048914507513856',REPORTS='1556941725119938633',
 LOGS='1091494080741122148',STAFFCMD='1113717920321773579',UPDATES='1079917581386924053',RACING_CAT='1508742900647723008')

def bold(s):
    o=''
    for ch in s:
        if 'A'<=ch<='Z': o+=chr(0x1D5D4+ord(ch)-65)
        elif 'a'<=ch<='z': o+=chr(0x1D5D4+ord(ch.upper())-65)
        elif '0'<=ch<='9': o+=chr(0x1D7EC+ord(ch)-48)
        else: o+=ch
    return o
def C(e,n): return f'{e}┃{bold(n)}'
def ow(i,a=0,d=0,t=0): return {'id':i,'type':t,'allow':str(a),'deny':str(d)}
norm=lambda o:sorted((x['id'],int(x['allow']),int(x['deny'])) for x in o)
def roles_by_name(): return {r['name']:r for r in req('GET',f'/guilds/{G}/roles')}
def chans(): return {c['id']:c for c in req('GET',f'/guilds/{G}/channels')}

def set_overwrites(cid,new,label):
    cur=req('GET',f'/channels/{cid}')['permission_overwrites']
    if norm(cur)==norm(new): print('  ok already',label); return
    write('PATCH',f'/channels/{cid}',{'permission_overwrites':new})
    if DRY: return
    got=req('GET',f'/channels/{cid}')['permission_overwrites']
    print('  ',label,'OK' if norm(got)==norm(new) else f'MISMATCH {got}')

def keep(cid,ids):
    """Existing overwrites for these ids (members/users or special roles we leave as they are)."""
    return [o for o in req('GET',f'/channels/{cid}')['permission_overwrites'] if o['id'] in ids]

# ---------- stages ----------
def stage_roles():
    rb=roles_by_name()
    for n in NEW_ROLES:
        if n in rb: print('  exists',n); continue
        write('POST',f'/guilds/{G}/roles',{'name':n,'permissions':'0','hoist':False,'mentionable':False})
        print('  created',n)
    if not DRY:
        rb=roles_by_name(); pos=rb['League Racer']['position']
        write('PATCH',f'/guilds/{G}/roles',[{'id':rb[n]['id'],'position':pos} for n in NEW_ROLES])
    for rid,body,label in ((F1,{'hoist':False,'color':0},'F1 plain'),(LMU,{'hoist':False,'color':0},'LMU plain')):  # LIVE RIGHT NOW sits above the bot, so Milan hoists it himself
        write('PATCH',f'/guilds/{G}/roles/{rid}',body); print('  ',label)
    if not DRY:
        for r in req('GET',f'/guilds/{G}/roles'):
            if r['name'] in NEW_ROLES+['F1','LMU','LIVE RIGHT NOW','League Racer']:
                print('   read back',r['position'],r['name'],'hoist',r['hoist'],'color',r['color'])

def all_members():
    mem=[];after='0'
    while True:
        b=req('GET',f'/guilds/{G}/members?limit=1000&after={after}'); mem+=b
        if len(b)<1000: return mem
        after=b[-1]['user']['id']

def stage_members():
    pit=roles_by_name()['Pit Wall']['id']
    humans=[m for m in all_members() if not m['user'].get('bot')]
    todo=[]
    for m in humans:
        if MEMBER not in m['roles']: todo.append((m['user']['id'],MEMBER,'Member'))
        if (YOUTUBE in m['roles'] or TIKTOK in m['roles']) and pit not in m['roles']: todo.append((m['user']['id'],pit,'Pit Wall'))
    print('  to add:',sum(1 for t in todo if t[2]=='Member'),'Member,',sum(1 for t in todo if t[2]=='Pit Wall'),'Pit Wall')
    for uid,rid,_ in todo: write('PUT',f'/guilds/{G}/members/{uid}/roles/{rid}')
    if DRY: return
    humans=[m for m in all_members() if not m['user'].get('bot')]
    print('   read back: without Member',sum(1 for m in humans if MEMBER not in m['roles']),
          '| YouTube/TikTok holders without Pit Wall',sum(1 for m in humans if (YOUTUBE in m['roles'] or TIKTOK in m['roles']) and pit not in m['roles']))

def locked(cid,roles,readonly=False,voice=False,extra_keep=()):
    """@everyone hidden; given roles (and Moderator) see it; bots keep posting access."""
    deny=VIEW|(CONNECT if voice else 0)
    allow_role=(VIEW|CONNECT) if voice else ((VIEW|65536) if readonly else TALK)
    o=[ow(EVERYONE,0,deny|(SEND if readonly else 0))]
    for r in roles: o.append(ow(r,allow_role))
    if MOD not in roles and MOD not in extra_keep: o.append(ow(MOD,VIEW|(CONNECT if voice else 0)))
    if not voice: o+= [ow(b,BOTSET) for b in BOT_ROLES]
    if extra_keep: o+=keep(cid,extra_keep)
    return o

def stage_channels():
    rb=roles_by_name(); PIT,PRIZE,STATS=(rb[n]['id'] for n in NEW_ROLES)
    cur=chans()
    stage_users=[o['id'] for o in cur[CH['NEWCONTENT']]['permission_overwrites'] if o['type']==1]
    watch_keep=[o['id'] for o in cur[CH['WATCH']]['permission_overwrites'] if o['id'] not in(EVERYONE,MEMBER)]
    plan=[
     ('CLIPS',locked(CH['CLIPS'],[MEMBER])),
     ('F1-NEWS',locked(CH['F1NEWS'],[F1],readonly=True)),
     ('STREAM-SCHEDULE',locked(CH['SCHEDULE'],[PIT],readonly=True)),
     ('NEW-CONTENT',locked(CH['NEWCONTENT'],[PIT],readonly=True,extra_keep=stage_users)),
     ('SELF-PROMOTION',locked(CH['PROMO'],[PIT])),
     ('GIVEAWAYS',locked(CH['GIVEAWAYS'],[PRIZE],readonly=True)),
     ('F1-SETUPS',locked(CH['SETUPS'],[F1])),
    ]
    for k in('PADDOCK','RR1','RR2','AFK'): plan.append((k,locked(CH[k],[MEMBER],voice=True)))
    plan.append(('STREAM-WATCH-PARTY',locked(CH['WATCH'],[MEMBER],voice=True,extra_keep=watch_keep)))
    stats_ow=[ow(EVERYONE,0,VIEW|CONNECT),ow(STATS,VIEW,CONNECT),ow(MOD,VIEW,CONNECT)]
    plan.append(('STATS category',stats_ow,STATS_CAT))
    for c in cur.values():
        if c.get('parent_id')==STATS_CAT: plan.append((c['name'],stats_ow,c['id']))
    for item in plan:
        label,o=item[0],item[1]; cid=item[2] if len(item)>2 else CH[{'F1-NEWS':'F1NEWS','STREAM-SCHEDULE':'SCHEDULE','NEW-CONTENT':'NEWCONTENT','SELF-PROMOTION':'PROMO','F1-SETUPS':'SETUPS','STREAM-WATCH-PARTY':'WATCH'}.get(label,label)]
        set_overwrites(cid,o,label)
    # rename Setups to F1 Setups
    if cur[CH['SETUPS']]['name']!=C('🛠️','F1-SETUPS'):
        write('PATCH',f"/channels/{CH['SETUPS']}",{'name':C('🛠️','F1-SETUPS')}); print('   renamed Setups to F1-SETUPS')
    # new channels (created with their permissions from the start)
    names={c['name'] for c in cur.values()}
    lmu_tags=['Le Mans','Spa','Monza','Sebring','Fuji','Portimão','Imola','Bahrain','Lusail','Interlagos','COTA','Paul Ricard',
              'Hypercar','LMGT3','LMP2','Dry','Wet','Race','Qualifying']
    new=[(C('🛠️','LMU-SETUPS'),15,{'available_tags':[{'name':t} for t in lmu_tags],
          'topic':'Share and find Le Mans Ultimate setups. Tag the track, class and conditions.'}),
         (C('🔎','LOOKING-FOR-RACE'),0,{'topic':'Looking for a lobby, practice partner or league? Post your game, platform and time here.'}),
         (C('📰','LMU-NEWS'),0,{'topic':'Le Mans Ultimate news and updates.'})]
    perms={C('🛠️','LMU-SETUPS'):[ow(EVERYONE,0,VIEW),ow(LMU,TALK),ow(MOD,TALK)],
           C('🔎','LOOKING-FOR-RACE'):[ow(EVERYONE,0,VIEW),ow(F1,TALK),ow(LMU,TALK),ow(MOD,TALK)],
           C('📰','LMU-NEWS'):[ow(EVERYONE,0,VIEW|SEND),ow(LMU,VIEW|65536),ow(MOD,VIEW)]+[ow(b,BOTSET) for b in BOT_ROLES]}
    for name,typ,extra in new:
        if name in names: print('  exists',name); continue
        body={'name':name,'type':typ,'parent_id':CH['RACING_CAT'],'permission_overwrites':perms[name]}; body.update(extra)
        g=write('POST',f'/guilds/{G}/channels',body)
        if g: print('   created',name,g['id'],'perms OK' if norm(req('GET',f"/channels/{g['id']}")['permission_overwrites'])==norm(perms[name]) else 'perms CHECK')
    # racing order
    if not DRY:
        cur=chans()
        order=[C('🏎️','F1-CHAT'),C('⏱️','LMU-CHAT'),C('🔎','LOOKING-FOR-RACE'),C('🛠️','F1-SETUPS'),C('🛠️','LMU-SETUPS'),
               C('🏆','COMMUNITY-LEAGUES'),C('📰','F1-NEWS'),C('📰','LMU-NEWS')]
        ids=[next(c['id'] for c in cur.values() if c['name']==n and c.get('parent_id')==CH['RACING_CAT']) for n in order]
        write('PATCH',f'/guilds/{G}/channels',[{'id':i,'position':p} for p,i in enumerate(ids)])
        print('   racing order:',[cur[i]['name'] for i in ids])

TOPICS={
 'GENERAL':'Main chat for the GreMi community. Racing, streams and everything in between.',
 'INTRO':'New here? Tell us who you are, what you race and what brought you to the paddock.',
 'FEEDBACK':'Ideas for the server, streams or videos. Every suggestion is read.',
 'CLIPS':'Your best moments, saves and crashes. Clips from streams welcome.',
 'GIVEAWAYS':'Giveaways and prizes. React to enter, winners are drawn by the bot.',
 'NEWCONTENT':'Automatic posts when GreMi goes live or uploads.',
 'SOCIALS':"All of GreMi's socials in one place.",
 'F1CHAT':'Everything Formula 1: the games and the real thing.',
 'LMUCHAT':'Le Mans Ultimate and endurance racing talk.',
 'F1NEWS':'Formula 1 and F1 game news, posted automatically.',
 'SUBCHAT':'A thank-you lounge for Twitch subscribers.',
 'STREAMERCHAT':'For community streamers to chat, collab and share tips.',
 'SUPPORT':'Questions or problems with the server? Ask here and a steward will help.',
 'BOTCMD':'Use bot commands here to keep the other chats clean.',
 'MODCHAT':'Staff discussion.','REPORTS':'Member reports and how they were handled.',
 'LOGS':'AutoMod alerts and bot logs.','STAFFCMD':'Staff bot commands.','UPDATES':'Discord community and safety updates.'}

def stage_extras():
    cur=chans()
    for k,t in TOPICS.items():
        if cur[CH[k]].get('topic')==t: continue
        write('PATCH',f"/channels/{CH[k]}",{'topic':t})
    print('   topics set:',len(TOPICS))
    if cur[CH['PROMO']].get('rate_limit_per_user')!=21600: write('PATCH',f"/channels/{CH['PROMO']}",{'rate_limit_per_user':21600})
    print('   self-promotion slowmode 6h')
    for k in('PADDOCK','RR1','RR2','AFK'):
        if cur[CH[k]].get('bitrate')!=96000: write('PATCH',f"/channels/{CH[k]}",{'bitrate':96000})
    for c in cur.values():
        if c.get('parent_id')==STATS_CAT and c.get('bitrate')!=96000: write('PATCH',f"/channels/{c['id']}",{'bitrate':96000})
    print('   voice 96 kbps')
    write('PATCH',f'/guilds/{G}',{'system_channel_id':CH['INTRO']}); print('   welcome messages go to INTRODUCTIONS')
    rules=req('GET',f'/guilds/{G}/auto-moderation/rules')
    alert={'type':2,'metadata':{'channel_id':CH['LOGS']}}
    want=[('Block slurs and sexual content',{'event_type':1,'trigger_type':4,'trigger_metadata':{'presets':[2,3]},'actions':[{'type':1,'metadata':{}},alert],'enabled':True}),
          ('Block suspected spam',{'event_type':1,'trigger_type':3,'actions':[{'type':1,'metadata':{}},alert],'enabled':True})]
    have={r['name']:r for r in rules}
    for name,body in want:
        if name in have: print('  exists',name); continue
        write('POST',f'/guilds/{G}/auto-moderation/rules',dict(body,name=name)); print('   AutoMod added:',name)
    # The existing mention-spam rule can't be edited through the API (404), so it stays as it is
    if not DRY:
        g=req('GET',f'/guilds/{G}'); print('   read back system channel', 'OK' if g['system_channel_id']==CH['INTRO'] else 'CHECK')
        print('   read back AutoMod:',[(r['name'],[a['type'] for a in r['actions']]) for r in req('GET',f'/guilds/{G}/auto-moderation/rules')])

if __name__=='__main__':
    {'roles':stage_roles,'members':stage_members,'channels':stage_channels,'extras':stage_extras}[STAGE]()
