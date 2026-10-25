# Word lists

`enable-3-5.txt` — every 3–5 letter word in **ENABLE** (Enhanced North
American Benchmark Lexicon, version 2, by Alan Beale and M. Cooke), lowercase
a–z, one per line. ENABLE is in the **public domain**. Copied from
<https://github.com/dolph/dictionary/blob/master/enable1.txt> on 2026-10-25.

It is the raw material for Joyscroll's word bank: `joyscroll_words_seed.py`
has a model sort each word into positive / wholesome / excluded once, and the
crossword fill (`joyscroll_crossword.py`) only ever uses the first two. It is
also the dictionary every word a model proposes is checked against, so a made-up
word never reaches a grid or a Joyword answer.
