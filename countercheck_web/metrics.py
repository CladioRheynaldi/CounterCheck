import re


def normalize(text):
    return " ".join(re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower()))


def edit_counts(reference, hypothesis):
    
    rows = [[(0, 0, 0) for _ in range(len(hypothesis)+1)] for _ in range(len(reference)+1)]
    for i in range(1, len(reference)+1):
        rows[i][0] = (0, i, 0)
    for j in range(1, len(hypothesis)+1):
        rows[0][j] = (0, 0, j)
    for i in range(1, len(reference)+1):
        for j in range(1, len(hypothesis)+1):
            if reference[i-1] == hypothesis[j-1]:
                rows[i][j] = rows[i-1][j-1]
            else:
                s,d,k = rows[i-1][j-1]
                substitution = (s+1,d,k)
                s,d,k = rows[i-1][j]
                deletion = (s,d+1,k)
                s,d,k = rows[i][j-1]
                insertion = (s,d,k+1)
                rows[i][j] = min([substitution,deletion,insertion], key=sum)
    s,d,k = rows[-1][-1]
    return dict(substitutions=s, deletions=d, insertions=k, errors=s+d+k, reference_units=len(reference))


def errors(reference, hypothesis):
    ref, hyp = normalize(reference), normalize(hypothesis)
    words = edit_counts(ref.split(), hyp.split())
    characters = edit_counts(list(ref.replace(" ", "")), list(hyp.replace(" ", "")))
    return dict(word_errors=words["errors"], character_errors=characters["errors"],
                reference_words=words["reference_units"], reference_characters=characters["reference_units"],
                wer=words["errors"]/max(1,words["reference_units"]), cer=characters["errors"]/max(1,characters["reference_units"]),
                word_operations=words, character_operations=characters)
