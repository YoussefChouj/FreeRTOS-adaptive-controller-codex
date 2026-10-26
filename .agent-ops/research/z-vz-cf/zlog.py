"""Parse a 10-24 telemetry.csv (long format) into 200 Hz arrays: h, vz, hd, U, ok, chg, rep."""
import collections, numpy as np
WANT={('1','t_ms'),('1','Ctrler.Z_posPID.FB'),('1','Ctrler.Z_ratePID.FB'),('1','Ctrler.Z_posPID.Des'),('2','t_ms'),('2','Ctrler.Z_ratePID.U')}
def load(f):
    fr=collections.defaultdict(list)
    with open(f) as fh:
        next(fh)
        for l in fh:
            _,sl,k,v=l.rstrip().split(',',3)
            if not k.startswith('slot'): continue
            kk=k.split('.',1)[1]
            if (sl,kk) in WANT:
                if kk=='t_ms': fr[sl].append({})
                if fr[sl]: fr[sl][-1][kk]=float(v)
    col=lambda sl,k: np.array([d.get(k,np.nan) for d in fr[sl]])
    t=col('1','t_ms'); h=col('1','Ctrler.Z_posPID.FB'); vz=col('1','Ctrler.Z_ratePID.FB'); hd=col('1','Ctrler.Z_posPID.Des')
    u2=dict(zip(col('2','t_ms'),col('2','Ctrler.Z_ratePID.U'))); U=np.array([u2.get(x,np.nan) for x in t])
    dt=np.diff(t); air=(h>0.15)&(hd>0.1)
    ok=np.r_[False,(dt>4.5)&(dt<5.5)]&air&np.r_[False,air[:-1]]
    dh=np.r_[np.nan,np.diff(h)]
    chg=ok&(np.abs(dh)>0.002); rep=ok&(np.abs(dh)<=0.002)
    return dict(t=t,h=h,vz=vz,hd=hd,U=U,ok=ok,chg=chg,rep=rep,dh=dh)
def runs(d):
    idx=np.where(d['chg'])[0]
    return np.array([b-a for a,b in zip(idx[:-1],idx[1:]) if d['ok'][a:b+1].all()])
