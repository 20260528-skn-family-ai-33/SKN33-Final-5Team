"""Locate a model-cited quote in the original text, tolerating whitespace changes only."""

def original_quote(text,quote):
    if not quote or not quote.strip():return None
    if quote in text:return quote
    positions=[i for i,c in enumerate(text) if not c.isspace()]
    compact=''.join(text[i] for i in positions);needle=''.join(c for c in quote if not c.isspace())
    if not needle:return None
    start=compact.find(needle)
    if start<0:return None
    return text[positions[start]:positions[start+len(needle)-1]+1]
