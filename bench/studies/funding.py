# Study (5 Nov 2026): does funding predict returns? Run from the repo root after fetching bench data (candles + funding).
import sys, gzip, json, random, math
from collections import defaultdict
from bisect import bisect_left
sys.path.insert(0,'bench')
import bench as B, engine as E
coins,_=B.load(); uni=B.universe_by_day(coins,100)
F=json.load(gzip.open('bench/data/funding_1d.json.gz','rt'))
fd={c:{d:r for d,r in v} for c,v in F.items()}
print('funding coins',len(F))
def trail(c, day, n=7):
    m=fd.get(c); 
    if not m: return None
    xs=[m.get(day-(k+1)*B.DAY) for k in range(n)]; xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if len(xs)>=n-1 else None
P=[('2024-25',lambda t:t<B.OOS_FROM),('2026',lambda t:t>=B.OOS_FROM)]
# 1) forward returns by cross-sectional funding quintile (weekly sampling, Mondays)
rows=defaultdict(list)
for name,v in coins.items():
    cs=v['candles']; ts=v['t']
    for i in range(10,len(cs)-28):
        d=cs[i]['t']
        if (d//B.DAY-4)%7: continue
        if d<1704067200000 or name not in uni.get(d,()): continue
        f=trail(name,d)
        if f is None: continue
        p=cs[i-1]['c']; rows[d].append((f,name,cs[i+6]['c']/p-1,cs[i+27]['c']/p-1))
for nm,sel in P:
    q=[[0,0,0] for _ in range(5)]
    for d,rs in rows.items():
        if not sel(d) or len(rs)<30: continue
        rs.sort(); n=len(rs); m7=sum(r[2] for r in rs)/n; m28=sum(r[3] for r in rs)/n
        for k,r in enumerate(rs):
            b=min(4,k*5//n); q[b][0]+=1; q[b][1]+=r[2]-m7; q[b][2]+=r[3]-m28
    print(nm,'funding quintile (1=most negative ... 5=most positive): excess 7d / 28d vs average coin')
    for b in range(5): print(f"   Q{b+1}: n={q[b][0]} 7d {100*q[b][1]/q[b][0]:+.2f}%  28d {100*q[b][2]/q[b][0]:+.2f}%")
# 2) bench: random entries filtered by funding extremes (hourly rate thresholds)
def fstrat(mode, seed, hi=0.0001, lo=-0.00002):
    rng=random.Random(seed)
    def f(ctx):
        if len(ctx.weeks)<27 or rng.random()>0.1: return None
        fr=trail(ctx.coin,ctx.day)
        if fr is None: return None
        if mode=='short_high' and fr>=hi: side='short'
        elif mode=='long_low' and fr<=lo: side='long'
        elif mode=='short_any': side='short'
        elif mode=='long_any': side='long'
        else: return None
        A=E.atr(ctx.weeks); sg=1 if side=='long' else -1; e=ctx.price
        return {'side':side,'entries':[e],'weights':[1.0],'stop':e-sg*A,'tps':[e+sg*1.5*A]}
    return f
end=max(v['t'][-1] for v in coins.values())+B.DAY
for mode in ('short_high','short_any','long_low','long_any'):
    trs=[]
    for s in (1,2,3): trs+=B.simulate(coins,fstrat(mode,s),1704067200000,end,ready_pct=None)
    print(mode, " | ".join(f"{nm} n={len([t for t in trs if sel(t['signal_ms'])])} {sum(t['r'] for t in trs if sel(t['signal_ms']))/max(1,len([t for t in trs if sel(t['signal_ms'])])):+.3f}" for nm,sel in P))
