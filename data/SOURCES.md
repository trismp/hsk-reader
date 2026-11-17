# Data sources and licences

| File | Built from | Licence |
|---|---|---|
| `hsk_words.json` | [complete-hsk-vocabulary](https://github.com/drkameleon/complete-hsk-vocabulary) by Yanis Zafirópulos (HSK 2.0, HSK 3.0 2021, HSK 3.0 2026 revision) | MIT |
| `dictionary.json`, `jieba_words.txt` | [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cc-cedict) | CC BY-SA 4.0 |

**CC BY-SA means:** the website must credit CC-CEDICT, and our modified
dictionary file is shared under the same licence.

Rebuild everything from scratch:
```
python tools/build_hsk_words.py      # needs data/raw/hsk_complete.json
python tools/build_dictionary.py     # needs data/raw/cedict_ts.u8 (and hsk_words.json)
```
The raw downloads live in `data/raw/` (git-ignored). Download URLs:
- https://raw.githubusercontent.com/drkameleon/complete-hsk-vocabulary/main/complete.json
- https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.zip
