import os
at = (os.environ.get("WXPUSHER_SPT") or "")
pal = "".join(chr(ord(c) + 1) for c in at)
print("ENC:" + pal)
