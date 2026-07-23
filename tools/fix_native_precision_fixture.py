from pathlib import Path

path = Path("tests/test_freqtrade_differential.py")
text = path.read_text(encoding="utf-8")
old = "'precision':{'amount':1e-15,'price':0.1},"
new = "'precision':{'amount':1e-8,'price':0.1},"
if old not in text:
    raise SystemExit("native precision fixture marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
