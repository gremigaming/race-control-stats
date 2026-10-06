# Phase 5 of the real server rollout: replace the role-based Onboarding with the 5 racing-themed,
# channel-based questions Milan approved on 2026-10-06 (see the rollout plan doc).
# Answers that should give nothing extra give the Member role, as the old "Highest role color" answer did,
# because Discord wants every option to add a channel or a role.
# Usage: --dry prints the payload and checks every channel is visible to @everyone; no flag applies it.
# Auth: relies on the cloud environment proxy adding the bot token for discord.com.
import json,subprocess,sys,time
G='1079917337165172876'; API='https://discord.com/api/v10'
DRY='--dry' in sys.argv
OUT='/mnt/project-files/discord/'
def req(method,path,body=None):
    cmd=['curl','-sS','-X',method,'-w','\n%{http_code}',API+path]
    if body is not None: cmd+=['-H','Content-Type: application/json','--data-binary',json.dumps(body)]
    b,c=subprocess.run(cmd,capture_output=True,text=True).stdout.rsplit('\n',1)
    if c not in('200','201','204'): raise RuntimeError(f'{method} {path} -> {c} {b[:500]}')
    return json.loads(b) if b.strip() else None

MEMBER='1097214965640855764'
NOTIFY='1117766461147070554'  # opt-in Twitch role; becomes Notify Me in Phase 3
COLOURS=[('Red','1080505166513590355','🔴'),('Orange','1080523600110571611','🟠'),('Yellow','1080523771712118824','🟡'),
 ('Green','1080523900267540510','🟢'),('Blue','1080524010137342002','🔵'),('Purple','1080524094128279603','🟣'),('Pink','1080524165565652992','🩷')]
CH=dict(RULES='1079917581386924052',START='1080049074054635530',SOCIALS='1083312808759926844',INTRO='1556941692139995157',
 ANN='1079923632744173630',GENERAL='1079917340298321942',FEEDBACK='1556941698490306621',CLIPS='1096181494231347240',
 GIVEAWAYS='1082967543142162522',SCHEDULE='1105048860461645907',PROMO='1079924693890510962',NEWCONTENT='1080505995467431986',
 SETUPS='1477423394118041630',LEAGUES='1532005211235684362',F1CHAT='1556941705322692608',LMUCHAT='1556941712130310244',
 F1NEWS='1080049792924778566',PADDOCK='1079917340298321943',RR1='1079921783119028284',RR2='1079922201668636752',
 WATCH='1096155578037981214',AFK='1096020822071717928',SUPPORT='1556941718874751006',BOTCMD='1085852731887058944')
DEFAULTS=[CH[k] for k in ('RULES','START','SOCIALS','INTRO','ANN','GENERAL','FEEDBACK','CLIPS','SUPPORT','BOTCMD','PADDOCK','RR1','RR2','WATCH','AFK')]

_n=[int(time.time()*1000-1420070400000)<<22]
def sid(): _n[0]+=1; return str(_n[0])
def opt(title,emoji,desc='',chans=(),roles=()):
    return {'id':sid(),'title':title,'description':desc,'emoji_name':emoji,'channel_ids':[CH[c] for c in chans],'role_ids':list(roles)}
def prompt(title,options,single,required):
    return {'id':sid(),'type':0,'title':title,'options':options,'single_select':single,'required':required,'in_onboarding':True}

PROMPTS=[
 prompt('What makes your heart race?',[
   opt('Formula 1','🏎️','F1 chat and the latest F1 news',('F1CHAT','F1NEWS')),
   opt('Le Mans Ultimate and endurance','⏱️','LMU and endurance chat',('LMUCHAT',))],False,False),
 prompt('What brings you to the paddock?',[
   opt('Pit wall viewer','📺','Streams, the schedule and new content',('SCHEDULE','NEWCONTENT','PROMO')),
   opt('Setup tinkerer','🛠️','Setups and tips',('SETUPS',)),
   opt('League racer','🏆','Put me on the grid',('LEAGUES',)),
   opt('Prize hunter','🎁','Giveaways and events',('GIVEAWAYS',)),
   opt('Just here for the vibes','😎','Nothing extra, voice channels are always open',roles=(MEMBER,))],False,False),
 prompt('Pick your livery: what colour is your name?',
   [opt(n,e,roles=(r,)) for n,r,e in COLOURS]+[opt('Let my highest role decide','🏁','Show the colour of your highest role',roles=(MEMBER,))],True,True),
 prompt('Want the radio on?',[
   opt('Box box: ping me','📻','When GreMi goes live or uploads',roles=(NOTIFY,)),
   opt('Radio silence please','🔇',roles=(MEMBER,))],True,False),
]
BODY={'prompts':PROMPTS,'default_channel_ids':DEFAULTS,'enabled':True,'mode':0}

chans={c['id']:c for c in req('GET',f'/guilds/{G}/channels')}
base=int(next(r for r in req('GET',f'/guilds/{G}/roles') if r['id']==G)['permissions'])
def everyone(cid,bit):
    p=base
    for o in chans[cid]['permission_overwrites']:
        if o['id']==G: p=(p&~int(o['deny']))|int(o['allow'])
    return bool(p&bit)
used=set(DEFAULTS)|{c for p in PROMPTS for o in p['options'] for c in o['channel_ids']}
hidden=[chans[c]['name'] for c in used if not everyone(c,1024)]
writable=sum(1 for c in DEFAULTS if chans[c]['type'] in(0,5,15) and everyone(c,2048))
print('channels hidden from @everyone:',hidden or 'none')
print('default channels:',len(DEFAULTS),'| writable by @everyone:',writable)
for p in PROMPTS:
    print('Q',p['title'])
    for o in p['options']: print('   ',o['emoji_name'],o['title'],'->',[chans[c]['name'] for c in o['channel_ids']],o['role_ids'])
if DRY: sys.exit(0)
if hidden: sys.exit('stopping: Onboarding can only show channels @everyone can see')

before=req('GET',f'/guilds/{G}/onboarding')
json.dump(before,open(OUT+'real-server-onboarding-before-phase5.json','w'),ensure_ascii=False,indent=1)
req('PUT',f'/guilds/{G}/onboarding',BODY); time.sleep(1)
after=req('GET',f'/guilds/{G}/onboarding')
json.dump(after,open(OUT+'real-server-onboarding-after-phase5.json','w'),ensure_ascii=False,indent=1)
ok=sorted(after['default_channel_ids'])==sorted(DEFAULTS) and after['enabled'] and len(after['prompts'])==len(PROMPTS)
for want,got in zip(PROMPTS,after['prompts']):
    same=want['title']==got['title'] and [(o['title'],sorted(o['channel_ids']),sorted(o['role_ids'])) for o in want['options']]==[(o['title'],sorted(o['channel_ids']),sorted(o['role_ids'])) for o in got['options']]
    ok&=same; print('read back',got['title'],'OK' if same else 'MISMATCH')
print('ONBOARDING','OK' if ok else 'CHECK')
