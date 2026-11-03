"""Compare Apple transliteration with the current conservative pypinyin rule."""
import json
import subprocess
import sys
from pypinyin import Style, pinyin

inputs = ["你好", "微信", "重庆", "长大", "银行", "音乐", "再见", "重"]
process = subprocess.run([sys.argv[1]], input=json.dumps(inputs, ensure_ascii=False),
                         text=True, capture_output=True, check=True)
outputs = json.loads(process.stdout)
for text, apple in zip(inputs, outputs):
    readings = pinyin(text, style=Style.NORMAL, heteronym=True)
    conservative = "".join(item[0] for item in readings) if all(len(item) == 1 for item in readings) else None
    print(json.dumps({"text": text, "apple": apple, "python": conservative}, ensure_ascii=False))
