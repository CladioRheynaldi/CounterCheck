pass
import re
from difflib import SequenceMatcher
from countercheck.orders import parse_order, NUMBER_PATTERN
from .settings import MENU


def draft(text):
    numeric_text=text
    for number,name in enumerate(NUMBER_NAMES[10:],11):
        numeric_text=re.sub(r'\b'+name+r'\b',str(number),numeric_text,flags=re.I)
    result = parse_order(numeric_text)
    items = [item.to_dict() for item in result.items]
    matches = list(re.finditer(r"\b(?:hot chocolate|cappuccinos?|americanos?|espressos?|lattes?|mochas?|teas?)\b", text.lower()))
    previous = 0
    for index, item in enumerate(items):
        prefix = text[previous:matches[index].start()] if index < len(matches) else ""
        if not re.search(r"\b(?:" + NUMBER_PATTERN + '|'+ '|'.join(NUMBER_NAMES)+r")\b", prefix, re.I):
            item["quantity"] = None
        for key in ("size", "temperature", "milk"):
            if item[key] == "unspecified":
                item[key] = None
        item["status"] = "review"
        if index < len(matches):
            previous = matches[index].end()
    warnings = [warning.replace("draft assumes one. Please check.", "quantity is unknown. Please confirm.") for warning in result.warnings]
    return dict(items=items, warnings=warnings, interpreted_text=text)


NUMBER_NAMES = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
FILLERS = set("i you your me my can could have get want would like need please with and the for make order it of thanks thank now a an i'd i'll".split())
ALIASES = {"size": {"little": "small", "regular": "medium", "big": "large"},
           "temperature": {"ice": "iced", "cold": "iced", "warm": "hot"},
           "quantity": {"won": "1", "too": "2", "to": "2", "fore": "4", "for": "4"},
           "extras": {"sugar free": "no sugar", "sugarfree": "no sugar"}}


def similarity(original, choice, category):
    source = re.sub(r"[^a-z0-9]", "", original.lower())
    target = re.sub(r"[^a-z0-9]", "", str(choice).lower())
    alias = ALIASES.get(category, {}).get(original.lower())
    if alias == str(choice):
        return 1.0
    if category == "quantity" and str(choice).isdigit():
        target = NUMBER_NAMES[int(choice)-1]
    
    variants = [source]
    if category == "size":
        variants += [source[:-2]] if source.endswith("er") else []
        variants += [source[:-1]] if source.endswith("r") else []
    if category == "drink" and source.endswith("s"):
        variants += [source[:-1]]
    return max(SequenceMatcher(None, value, target).ratio() for value in variants)


def uncertain_slots(text):
    terms = list(re.finditer(r"[a-z]+(?:'[a-z]+)?|\d+", text.lower()))
    values = [match.group() for match in terms]
    spans, claimed, anchors = [], set(), []
    def add(category, first, end):
        if first < end and not claimed.intersection(range(first,end)):
            spans.append(dict(category=category,start=terms[first].start(),end=terms[end-1].end()))
            claimed.update(range(first,end))
    
    
    i=0
    while i < len(values):
        found=None
        for length in (2,1):
            if i+length>len(values): continue
            phrase=" ".join(values[i:i+length])
            canonical=next((drink for drink in MENU['drink'] if phrase in (drink,drink+'s')),None)
            if canonical:
                found=(i,i+length,True);break
        if found is None and values[i] not in FILLERS and not values[i].isdigit():
            best=max(similarity(values[i],drink,'drink') for drink in MENU['drink'])
            if best >= .72:
                found=(i,i+1,False)
        if found:
            first,end,exact=found;anchors.append((first,end))
            if not exact: add('drink',first,end)
            i=end
        else: i+=1
    previous=0
    for first,end in anchors:
        boundaries=[i for i in range(previous,first) if values[i]=='and']
        start=boundaries[-1]+1 if boundaries else previous
        quantities=[i for i in range(start,first) if values[i] in NUMBER_NAMES or values[i].isdigit() or values[i] in {'a','an'}]
        if quantities: start=quantities[-1]+1
        extra_words=set(' '.join(MENU['extras']).split())
        for i in range(start,first):
            value=values[i]
            if i in claimed: continue
            next_is_modifier = i+1==first or values[i+1] in MENU['size']+MENU['temperature'] or max(similarity(values[i+1],c,'size') for c in MENU['size'])>=.7
            if value in ALIASES['quantity'] and next_is_modifier and not any(v in NUMBER_NAMES or v.isdigit() for v in values[start:first]):
                add('quantity',i,i+1);continue
            if value in FILLERS or value in NUMBER_NAMES or value.isdigit() or value in MENU['size']+MENU['temperature'] or value in extra_words: continue
            scores={category:max(similarity(value,choice,category) for choice in MENU[category]) for category in ('size','temperature','extras')}
            category=max(scores,key=scores.get)
            
            if scores[category]>=.45: add(category,i,i+1)
        previous=end
    milk_bases={value.split()[0] for value in MENU['milk']}
    for i,value in enumerate(values):
        if value=='milk' and i and values[i-1] not in milk_bases | FILLERS:
            add('milk',i-1,i+1)
        elif value.endswith('milk') and value!='milk' and max(similarity(value,c,'milk') for c in MENU['milk'])>=.65:
            add('milk',i,i+1)
    
    if anchors:
        first_drink=anchors[0][1]
        known=set(' '.join(c for k in ('drink','size','temperature','milk','extras') for c in MENU[k]).split())
        for i in range(first_drink,len(values)):
            for length in (2,1):
                phrase=' '.join(values[i:i+length])
                if i+length>len(values) or phrase in MENU['extras']: continue
                if phrase in ALIASES['extras']:
                    add('extras',i,i+length);break
                if length==2 and values[i] in {c.split()[0] for c in MENU['extras'] if ' ' in c} and max(similarity(phrase,c,'extras') for c in MENU['extras'])>=.8:
                    add('extras',i,i+length);break
                if length==1 and values[i] not in known | FILLERS and max(similarity(phrase,c,'extras') for c in MENU['extras'])>=.72:
                    add('extras',i,i+1)
    if not anchors:
        position=0
        while position<len(values):
            value=values[position]
            if value in FILLERS or value in NUMBER_NAMES or value.isdigit() or value in MENU['size']+MENU['temperature']:
                position+=1;continue
            scores={category:max(similarity(value,c,category) for c in MENU[category]) for category in ('size','temperature')}
            category=max(scores,key=scores.get)
            if scores[category]>=.7:
                add(category,position,position+1);position+=1;continue
            break
        end=position
        while end<len(values) and values[end] not in {'with','and','please','milk','thank','thanks'}: end+=1
        if 0<end-position<=3 and set(values[:position]) & {'get','have','want','like','a','one','two'}:
            add('drink',position,end)
    return sorted(spans,key=lambda span:span['start'])


def ground(engine, audio, raw, words=None):
    result = draft(raw)
    result.update(raw_text=raw, menu_decisions=[])
    negation_check=re.sub(r'\bno\s+(\w+)\b',lambda m:'no sugar' if SequenceMatcher(None,m.group(1),'sugar').ratio()>=.8 else m.group(),raw.lower())
    if re.search(r"\b(not|without|don't|sorry|actually|instead|cancel|remove)\b|\bno\b(?! sugar\b)", negation_check):
        return result
    replacements = []
    for span in uncertain_slots(raw)[:12]:
        category = span["category"]
        choices = MENU[category]
        original = raw[span["start"]:span["end"]]
        candidate_sentences = {}
        for choice in choices:
            choice = str(choice)
            candidate_sentences[raw[:span["start"]] + choice + raw[span["end"]:]] = choice
        ranked = engine.score_candidates(audio, list(candidate_sentences))
        best_audio=max(row['score'] for row in ranked)
        for row in ranked:
            value=candidate_sentences[row['text']]
            row['similarity']=similarity(original,value,category)
            
            
            row['normalization_bonus']=1.5 if row['similarity']>=.99 else 0.0
            row['menu_score']=max(-2.0,row['score']-best_audio)+2.5*row['similarity']+row['normalization_bonus']
        ranked.sort(key=lambda row:row['menu_score'],reverse=True)
        top=ranked[0]
        margin=top['menu_score']-ranked[1]['menu_score'] if len(ranked)>1 else 0
        accepted=True  
        weak=category=='quantity' or margin<.15 or top['score'] < -1.8 or top['similarity']<.6
        decision = dict(category=category, original=original, proposed=candidate_sentences[top["text"]],
                        accepted=accepted, score_margin=margin,
                        confidence_kind="uncalibrated_audio_and_menu_similarity_ranking",low_confidence=weak,
                        selection_mode="best_menu_choice", 
                        candidates=[dict(value=candidate_sentences[row["text"]],score=row['menu_score'],audio_score=row['score'],similarity=row['similarity'],original=False) for row in ranked],
                        status="best menu guess — low evidence; edit if wrong" if weak else "menu match — edit if wrong")
        result["menu_decisions"].append(decision)
        if accepted:
            replacements.append((span["start"], span["end"], decision["proposed"]))
    interpreted = raw
    claimed = set()
    for start, end, value in sorted(replacements, reverse=True):
        if not claimed.intersection(range(start, end)):
            interpreted = interpreted[:start] + value + interpreted[end:]
            claimed.update(range(start, end))
    updated = draft(interpreted)
    result.update(items=updated["items"], interpreted_text=interpreted,
                  warnings=updated["warnings"] + [f"{d['category']}: {d['original']} → {d['proposed']}; {d['status']}" for d in result["menu_decisions"]])
    return result
