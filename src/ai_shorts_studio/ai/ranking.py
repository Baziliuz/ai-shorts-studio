"""Deterministic evidence ranking, deliberately separate from speech recognition.
Scores are heuristic signals, not event/emotion/virality recognition.
"""
import re
from uuid import uuid4

CUES=re.compile(r'ничего себе|неожидан|получилось|смотри|важн|ошибк|секрет|почему|побед|офиг|wow|amazing|finally|look|watch|mistake|important',re.I)

def candidates(segments,signals,duration_us,target_us=30_000_000,min_us=10_000_000,max_us=30_000_000,limit=10):
    if not 0 < min_us <= target_us <= max_us or not 1 <= limit <= 30:
        raise ValueError('Проверьте минимальную, целевую и максимальную длину клипа')
    anchors=[]
    for s in segments:
        if not 0<=s['start_us']<s['end_us']<=duration_us: raise ValueError('Некорректный сегмент речи')
        cue=CUES.search(s['text'])
        anchors.append((s['start_us'],35 if cue else 0, f'Слово/фраза в речи: «{cue.group()}»' if cue else None))
    anchors += [(s['time_us'],0,None) for s in signals if s.get('loudness',0)>=.6 or s.get('motion',0)>=.6]
    proposals=[]
    for anchor,cue_score,cue in anchors:
        if not 0<=anchor<duration_us: continue
        start=max(0,anchor-5_000_000)
        end=min(duration_us,start+target_us)
        if end-start < min_us: start=max(0,end-target_us)
        # Prefer a real utterance ending near the target while keeping strict limits.
        endings=[s['end_us'] for s in segments if start+min_us<=s['end_us']<=min(duration_us,start+max_us)]
        if endings: end=min(endings,key=lambda t:abs(t-(start+target_us)))
        if not min_us<=end-start<=max_us: continue
        speech=[s for s in segments if s['start_us']<end and s['end_us']>start]
        nearby=[s for s in signals if abs(s['time_us']-anchor)<=3_000_000]
        loud=max((s.get('loudness',0) for s in nearby),default=0)
        motion=max((s.get('motion',0) for s in nearby),default=0)
        score=min(100,round(cue_score+(15 if speech else 0)+30*loud+20*motion))
        if score<30: continue
        evidence=([cue] if cue else [])
        if speech: evidence.append('Есть распознанная речь')
        if loud>=.6: evidence.append('Повышение громкости относительно этой записи')
        if motion>=.6: evidence.append('Повышенная разница кадров (1 кадр/сек.)')
        text=' '.join(s['text'].strip() for s in speech).strip()
        # A title is a quote from real segments, never a generated event claim.
        title=(text[:72]+'…' if len(text)>72 else text) or f'Фрагмент {anchor//1_000_000} сек.'
        proposals.append(dict(id=uuid4().hex,name=title,start_us=start,end_us=end,score=score,
            segment_ids=[s['id'] for s in speech],evidence=evidence,
            explanation='Эвристическая оценка по измеримым сигналам; проверьте контекст.',
            text=text[:1200],status='pending'))
    selected=[]
    for item in sorted(proposals,key=lambda c:c['score'],reverse=True):
        if any(max(0,min(item['end_us'],other['end_us'])-max(item['start_us'],other['start_us'])) /
               min(item['end_us']-item['start_us'],other['end_us']-other['start_us'])>.5 for other in selected): continue
        selected.append(item)
        if len(selected)==limit: break
    return selected
