"""Offline hybrid retrieval: BM25 + character TF-IDF + RRF + MMR."""
from collections import Counter
from functools import lru_cache
import math
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

EXPANSIONS = {'方法':'method approach methodology','实验':'experiment evaluation results','数据集':'dataset benchmark','局限':'limitation future work','创新':'contribution novelty','摘要':'abstract introduction','复现':'implementation reproducibility hyperparameter','消融':'ablation','指标':'metric accuracy precision recall F1','检索':'retrieval search','记忆':'memory','训练':'training optimization'}

def expand_query(query):
    return (query + ' ' + ' '.join(v for k,v in EXPANSIONS.items() if k in query)).strip()

def tokens(text):
    parts = re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower())
    out=[]
    for part in parts:
        if '\u4e00' <= part[0] <= '\u9fff':
            out.extend(part[i:i+2] for i in range(len(part)-1))
            if len(part)==1: out.append(part)
        else: out.append(part)
    return out

@lru_cache(maxsize=4)
def build_index(texts):
    vectorizer=TfidfVectorizer(analyzer='char',ngram_range=(2,4),max_features=60000,sublinear_tf=True)
    matrix=vectorizer.fit_transform(texts)
    counts=[Counter(tokens(t)) for t in texts]
    lengths=np.array([sum(c.values()) for c in counts],dtype=float)
    df=Counter(t for c in counts for t in c)
    return vectorizer,matrix,counts,lengths,df

def retrieve(rows,query,limit=6,mode='hybrid',expand=True,diversity=True):
    if not rows or not query.strip(): return []
    if mode not in ('hybrid','tfidf','bm25'): raise ValueError('未知检索模式')
    q=expand_query(query) if expand else query
    try: v,m,counts,lengths,df=build_index(tuple(r['text'] for r in rows))
    except ValueError: return []
    tf=cosine_similarity(v.transform([q]),m).ravel()
    bm=np.zeros(len(rows))
    average=max(float(lengths.mean()),1.)
    for t in set(tokens(q)):
        freq=np.array([c.get(t,0) for c in counts],dtype=float)
        idf=math.log(1+(len(rows)-df.get(t,0)+.5)/(df.get(t,0)+.5))
        bm+=idf*(freq*2.5)/(freq+1.5*(.25+.75*lengths/average))
    # Require a token hit or a meaningful character match to avoid noise.
    valid=np.where((bm>0)|(tf>=.06))[0]
    if mode=='bm25': valid=np.where(bm>0)[0]
    if mode=='tfidf': valid=np.where(tf>=.06)[0]
    fused=np.zeros(len(rows))
    if mode=='hybrid':
        for scores in (tf,bm):
            ranked=sorted((int(i) for i in valid if scores[i]>0),key=lambda i:(-scores[i],i))
            for rank,i in enumerate(ranked,1): fused[i]+=1/(60+rank)
    else: fused=tf.copy() if mode=='tfidf' else bm.copy()
    candidates=sorted((int(i) for i in valid),key=lambda i:(-fused[i],i))[:60]
    chosen=[]
    top=max((fused[i] for i in candidates),default=1.)
    while candidates and len(chosen)<max(1,min(int(limit),12)):
        if diversity and chosen:
            similarity=cosine_similarity(m[candidates],m[chosen]).max(axis=1)
            index=max(range(len(candidates)),key=lambda j:.8*fused[candidates[j]]/top-.2*similarity[j])
        else: index=0
        chosen.append(candidates.pop(index))
    result=[]
    for i in chosen:
        row=dict(rows[i])
        row.update(score=round(float(fused[i]),6),tfidf=round(float(tf[i]),4),bm25=round(float(bm[i]),4),citation=f"[{row['id']}]",retrieval=mode,expanded_query=q.strip())
        result.append(row)
    return result
